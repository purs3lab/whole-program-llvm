import logging
import collections
import os
import re
import sys

# Internal logger
_logger = logging.getLogger(__name__)

# Flag for dumping
DUMPING = False

# This class applies filters to GCC argument lists.  It has a few
# default arguments that it records, but does not modify the argument
# list at all.  It can be subclassed to change this behavior.
#
# The idea is that all flags accepting a parameter must be specified
# so that they know to consume an extra token from the input stream.
# Flags and arguments can be recorded in any way desired by providing
# a callback.  Each callback/flag has an arity specified - zero arity
# flags (such as -v) are provided to their callback as-is.  Higher
# arities remove the appropriate number of arguments from the list and
# pass them to the callback with the flag.
#
# Most flags can be handled with a simple lookup in a table - these
# are exact matches.  Other flags are more complex and can be
# recognized by regular expressions.  All regular expressions must be
# tried, obviously.  The first one that matches is taken, and no order
# is specified.  Try to avoid overlapping patterns.
class CopaArgumentListFilter:
    def __init__(self, inputList, exactMatches={}, patternMatches={}):
        defaultArgExactMatches = {

            '-' : (0, CopaArgumentListFilter.standardInCallback),

            '-o' : (1, CopaArgumentListFilter.outputFileCallback),
            '-c' : (0, CopaArgumentListFilter.compileOnlyCallback),
            '-E' : (0, CopaArgumentListFilter.preprocessOnlyCallback),
            '-S' : (0, CopaArgumentListFilter.assembleOnlyCallback),

            '--verbose' : (0, CopaArgumentListFilter.verboseFlagCallback),

            #iam: presumably the len(inputFiles) == 0 in this case
            '--version' : (0, CopaArgumentListFilter.compileOnlyCallback),
            '-v' : (0, CopaArgumentListFilter.compileOnlyCallback),

            # Optimization
            '-O' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            '-O0' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            # allow only -O0 and disable all other optimization levels.
            '-O1' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            '-O2' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            '-O3' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            '-Os' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            '-Ofast' : (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            '-Og' : (0, CopaArgumentListFilter.warningLinkUnaryCallback)

        }

        #
        # Patterns for other command-line arguments:
        # - inputFiles
        # - objectFiles (suffix .o)
        # - libraries + linker options as in -lxxx -Lpath or -Wl,xxxx
        # - preprocessor options as in -DXXX -Ipath
        # - compiler warning options: -W....
        # - optimiziation and other flags: -f...
        #
        defaultArgPatterns = {
            r'^.+\.(c|cc|cpp|C|cxx|i|s|S|bc)$' : (0, CopaArgumentListFilter.inputFileCallback),
            # FORTRAN file types
            r'^.+\.([fF](|[0-9][0-9]|or|OR|pp|PP))$' : (0, CopaArgumentListFilter.inputFileCallback),
            #iam: the object file recogition is not really very robust, object files
            # should be determined by their existance and contents...
            r'^.+\.(o|lo|So|so|po|a|dylib)$' : (0, CopaArgumentListFilter.objectFileCallback),
            #iam: library.so.4.5.6 probably need a similar pattern for .dylib too.
            r'^.+\.dylib(\.\d)+$' : (0, CopaArgumentListFilter.objectFileCallback),
            r'^.+\.(So|so)(\.\d)+$' : (0, CopaArgumentListFilter.objectFileCallback),
            r'^-O[1-9]+$': (0, CopaArgumentListFilter.warningLinkUnaryCallback),
            r'^-O[0-9][0-9]+$' : (0, CopaArgumentListFilter.warningLinkUnaryCallback)

        }

        #iam: try and keep track of the files, input object, and output
        self.inputList = inputList
        self.inputFiles = []
        self.objectFiles = []
        self.outputFilename = None

        #iam: try and split the args into linker and compiler switches
        self.compileArgs = []
        self.linkArgs = []
        # currently only dead_strip belongs here; but I guess there could be more.
        self.forbiddenArgs = []


        self.isVerbose = False
        self.isDependencyOnly = False
        self.isPreprocessOnly = False
        self.isAssembleOnly = False
        self.isAssembly = False
        self.isCompileOnly = False
        self.isEmitLLVM = False
        self.isStandardIn = False

        argExactMatches = dict(defaultArgExactMatches)
        argExactMatches.update(exactMatches)
        argPatterns = dict(defaultArgPatterns)
        argPatterns.update(patternMatches)

        self._inputArgs = collections.deque(inputList)

        #iam: parse the cmd line, bailing if we discover that there will be no second phase.
        while (self._inputArgs and
               not (self.isAssembleOnly or
                    self.isPreprocessOnly)):
            # Get the next argument
            currentItem = self._inputArgs.popleft()
            _logger.debug('Trying to match item %s', currentItem)
            # First, see if this exact flag has a handler in the table.
            # This is a cheap test.  Otherwise, see if the input matches
            # some pattern with a handler that we recognize
            if currentItem in argExactMatches:
                (arity, handler) = argExactMatches[currentItem]
                flagArgs = self._shiftArgs(arity)
                handler(self, currentItem, *flagArgs)
            elif currentItem == '-Wl,--start-group':
                linkingGroup = [currentItem]
                terminated = False
                while self._inputArgs:
                    groupCurrent = self._inputArgs.popleft()
                    linkingGroup.append(groupCurrent)
                    if groupCurrent == "-Wl,--end-group":
                        terminated = True
                        break
                if not terminated:
                    _logger.warning('Did not find a closing "-Wl,--end-group" to match "-Wl,--start-group"')
                self.linkingGroupCallback(linkingGroup)
            else:
                matched = False
                for pattern, (arity, handler) in argPatterns.items():
                    if re.match(pattern, currentItem):
                        flagArgs = self._shiftArgs(arity)
                        handler(self, currentItem, *flagArgs)
                        matched = True
                        break
                # If no action has been specified, this is a zero-argument
                # flag that we should just keep.
                if not matched:
                    _logger.warning('Did not recognize the compiler flag "%s"', currentItem)
                    self.compileUnaryCallback(currentItem)

        if DUMPING:
            self.dump()


    def skipBitcodeGeneration(self):
        retval = (True, "Non-bitcode compilation")
        return retval

    def _shiftArgs(self, nargs):
        ret = []
        while nargs > 0:
            a = self._inputArgs.popleft()
            ret.append(a)
            nargs = nargs - 1
        return ret


    def standardInCallback(self, flag):
        _logger.debug('standardInCallback: %s', flag)
        self.isStandardIn = True

    def abortUnaryCallback(self, flag):
        _logger.warning('Out of context experience: "%s" "%s"', str(self.inputList), flag)
        sys.exit(1)

    def inputFileCallback(self, infile):
        _logger.debug('Input file: %s', infile)
        self.inputFiles.append(infile)
        if re.search('\\.(s|S)$', infile):
            self.isAssembly = True

    def outputFileCallback(self, flag, filename):
        _logger.debug('outputFileCallback: %s %s', flag, filename)
        self.outputFilename = filename

    def objectFileCallback(self, objfile):
        _logger.debug('objectFileCallback: %s', objfile)
        self.objectFiles.append(objfile)

    def preprocessOnlyCallback(self, flag):
        _logger.debug('preprocessOnlyCallback: %s', flag)
        self.isPreprocessOnly = True

    def dependencyOnlyCallback(self, flag):
        _logger.debug('dependencyOnlyCallback: %s', flag)
        self.isDependencyOnly = True
        self.compileArgs.append(flag)

    def assembleOnlyCallback(self, flag):
        _logger.debug('assembleOnlyCallback: %s', flag)
        self.isAssembleOnly = True

    def verboseFlagCallback(self, flag):
        _logger.debug('verboseFlagCallback: %s', flag)
        self.isVerbose = True

    def compileOnlyCallback(self, flag):
        _logger.debug('compileOnlyCallback: %s', flag)
        self.isCompileOnly = True

    def emitLLVMCallback(self, flag):
        _logger.debug('emitLLVMCallback: %s', flag)
        self.isEmitLLVM = True
        self.isCompileOnly = True

    def linkUnaryCallback(self, flag):
        _logger.debug('linkUnaryCallback: %s', flag)
        self.linkArgs.append(flag)

    def compileUnaryCallback(self, flag):
        _logger.debug('compileUnaryCallback: %s', flag)
        self.compileArgs.append(flag)

    def warningLinkUnaryCallback(self, flag):
        _logger.debug('warningLinkUnaryCallback: %s', flag)
        _logger.warning('The flag "%s" cannot be used with this tool; we are ignoring it', flag)
        self.forbiddenArgs.append(flag)

    def defaultBinaryCallback(self, flag, arg):
        _logger.warning('Ignoring compiler arg pair: "%s %s"', flag, arg)

    def dependencyBinaryCallback(self, flag, arg):
        _logger.debug('dependencyBinaryCallback: %s %s', flag, arg)
        self.isDependencyOnly = True
        self.compileArgs.append(flag)
        self.compileArgs.append(arg)

    def compileBinaryCallback(self, flag, arg):
        _logger.debug('compileBinaryCallback: %s %s', flag, arg)
        self.compileArgs.append(flag)
        self.compileArgs.append(arg)


    def linkBinaryCallback(self, flag, arg):
        _logger.debug('linkBinaryCallback: %s %s', flag, arg)
        self.linkArgs.append(flag)
        self.linkArgs.append(arg)

    def linkingGroupCallback(self, args):
        _logger.debug('linkingGroupCallback: %s', args)
        self.linkArgs.extend(args)

    #flags common to both linking and compiling (coverage for example)
    def compileLinkUnaryCallback(self, flag):
        _logger.debug('compileLinkUnaryCallback: %s', flag)
        self.compileArgs.append(flag)
        self.linkArgs.append(flag)

    def getOutputFilename(self):
        if self.outputFilename is not None:
            return self.outputFilename
        if self.isCompileOnly:
            #iam: -c but no -o, therefore the obj should end up in the cwd.
            (_, base) = os.path.split(self.inputFiles[0])
            (root, _) = os.path.splitext(base)
            return f'{root}.o'
        return 'a.out'

    def getBitcodeFileName(self):
        (dirs, baseFile) = os.path.split(self.getOutputFilename())
        bcfilename = os.path.join(dirs, f'.{baseFile}.bc')
        return bcfilename

    # iam: returns a pair [objectFilename, bitcodeFilename] i.e .o and .bc.
    # the hidden flag determines whether the objectFile is hidden like the
    # bitcodeFile is (starts with a '.'), use the logging level & DUMPING flag to get a sense
    # of what is being written out.
    def getArtifactNames(self, srcFile, hidden=False):
        (_, srcbase) = os.path.split(srcFile)
        (srcroot, _) = os.path.splitext(srcbase)
        if hidden:
            objbase = f'.{srcroot}.o'
        else:
            objbase = f'{srcroot}.o'
        bcbase = f'.{srcroot}.o.bc'
        return [objbase, bcbase]

    #iam: for printing our partitioning of the args
    def dump(self):
        efn = sys.stderr.write
        efn(f'\ncompileArgs: {self.compileArgs}\ninputFiles: {self.inputFiles}\nlinkArgs: {self.linkArgs}\n')
        efn(f'\nobjectFiles: {self.objectFiles}\noutputFilename: {self.outputFilename}\n')
        for srcFile in self.inputFiles:
            efn(f'\nsrcFile: {srcFile}\n')
            (objFile, bcFile) = self.getArtifactNames(srcFile)
            efn(f'\n{srcFile} ===> ({objFile}, {bcFile})\n')
        efn(f'\nFlags:\nisVerbose = {self.isVerbose}\n')
        efn(f'isDependencyOnly = {self.isDependencyOnly}\n')
        efn(f'isPreprocessOnly = {self.isPreprocessOnly}\n')
        efn(f'isAssembleOnly = {self.isAssembleOnly}\n')
        efn(f'isAssembly = {self.isAssembly}\n')
        efn(f'isCompileOnly = {self.isCompileOnly}\n')
        efn(f'isEmitLLVM = {self.isEmitLLVM}\n')
        efn(f'isStandardIn = {self.isStandardIn}\n')
