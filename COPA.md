# Using wllvmcopa (and wllvmcopa++)
This is the modified wllvm that supports restricting compiler flags to all compiler invocations.
## Usage
```
export COPA_COMPILER=gcc
export COPA_CXX_COMPILER=g++
CC=wllvmcopa CXX=wllvmcopa++ ./configure
make
```
The above command will remove all optimization flags and make sure that the compilation is done with no optimizatons (i.e. `-O0`).

## Configuring optimization flags
There are 2 files that need to be created. 
  * First, a file containing **ALL** possible optimization flags (one per each line): The path for this file should be provided through the environment variable: `COPA_ALL_OPTIMIZATION_FLAGS_FILE` . **Note: This is one time thing that need to be done for each compiler**. 

Example: `export COPA_ALL_OPTIMIZATION_FLAGS_FILE=/home/machiry/all_gcc_opts.txt`
  > all_gcc_opts.txt:
 ```
 -falign-functions 
 -falign-functions
 -falign-jumps 
 -falign-jumps
 ...
 ```
  * Second, a file containing the optimization flags that should be enabled (one per each line): The path for this file should be provided through the environment variable: `COPA_CURR_OPTIMIZATION_FLAGS_FILE`. This should be set by the post processor after figuring out which options to enable.
 
 Example: `export COPA_CURR_OPTIMIZATION_FLAGS_FILE=/home/afl_fuzz/curr_opts.txt`
  > curr_opts.txt:
 ```
 -fcode-hoisting 
 -fcaller-saves
 ```

Providing the above files makes wllvm remove all optimizations and enable only those optimization provided through `COPA_CURR_OPTIMIZATION_FLAGS_FILE` environment variable.
