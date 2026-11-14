#!/bin/bash

# Install apt dependencies
sudo apt update -y
sudo apt upgrade -y
sudo apt install -y \
     python3-pip python3-venv python-is-python3 build-essential \
     cmake gfortran g++ libpugixml-dev libpng-dev libhdf5-dev  \
     libeigen3-dev libnetcdf-dev libtbb-dev libglfw3-dev

# Create and activate Python virtual environment
mkdir venv
python -m venv venv/openmc_env
source venv/openmc_env/bin/activate
pip install -U pip
pip install wheel

EMBREE_INSTALL_DIR=$HOME/software/embree
MOAB_INSTALL_DIR=$HOME/software/moab
DD_INSTALL_DIR=$HOME/software/double-down
DAGMC_INSTALL_DIR=$HOME/software/dagmc
NJOY_INSTALL_DIR=$HOME/software/njoy2016
OPENMC_INSTALL_DIR=$HOME/software/openmc

# Install NJOY
git clone --single-branch --depth 1 https://github.com/njoy/NJOY2016
pushd NJOY2016
mkdir build && pushd build
cmake -Dstatic=on \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCMAKE_INSTALL_PREFIX=$NJOY_INSTALL_DIR \
      ..
make 2>/dev/null -j2 install
popd && popd
rm -rf $HOME/NJOY2016

# Clone and install EMBREE
git clone --single-branch -b v3.12.2 --depth 1 https://github.com/embree/embree
pushd embree
mkdir build && pushd build
cmake -DCMAKE_INSTALL_PREFIX=$EMBREE_INSTALL_DIR \
      -DEMBREE_MAX_ISA=NONE \
      -DEMBREE_ISA_SSE42=ON \
      -DEMBREE_ISPC_SUPPORT=OFF \
      ..
make 2>/dev/null -j2 install
popd && popd
rm -fr $HOME/embree

# Clone and install MOAB
pip install -U numpy cython
git clone  --single-branch -b 5.3.0 --depth 1 https://bitbucket.org/fathomteam/moab
pushd moab
mkdir build && pushd build
cmake -DCMAKE_INSTALL_PREFIX=$MOAB_INSTALL_DIR \
      -DENABLE_HDF5=ON \
      -DENABLE_NETCDF=ON \
      -DENABLE_FORTRAN=OFF \
      -DENABLE_BLASLAPACK=OFF \
      -DENABLE_PYMOAB=ON \
      -DBUILD_SHARED_LIBS=ON \
      ..
make 2>/dev/null -j2 install
pushd pymoab
bash install.sh
python setup.py install
popd && popd && popd
rm -rf $HOME/moab

# Clone and install Double-Down
git clone --single-branch -b v1.0.0 --depth 1 https://github.com/pshriwise/double-down
pushd double-down
mkdir build && pushd build
cmake -DCMAKE_INSTALL_PREFIX=$DD_INSTALL_DIR \
      -DMOAB_DIR=$MOAB_INSTALL_DIR \
      -DEMBREE_DIR=$EMBREE_INSTALL_DIR \
      ..
make 2>/dev/null -j2 install
popd && popd
rm -rf $HOME/double-down

# Clone and install DAGMC
git clone --single-branch -b v3.2.1 --depth 1 https://github.com/svalinn/DAGMC
pushd DAGMC
mkdir build && pushd build
cmake -DCMAKE_INSTALL_PREFIX=$DAGMC_INSTALL_DIR \
      -DCMAKE_PREFIX_PATH=$DD_INSTALL_DIR/lib \
      -DBUILD_TALLY=OFF \
      -DBUILD_STATIC_LIBS=OFF \
      -DBUILD_STATIC_EXE=OFF \
      -DMOAB_DIR=$MOAB_INSTALL_DIR \
      -DDOUBLE_DOWN=ON \
      -DDOUBLE_DOWN_DIR=$DD_INSTALL_DIR \
      ..
make 2>/dev/null -j2 install
popd && popd
rm -fr $HOME/DAGMC

# Install OpenMC
git clone https://github.com/openmc-dev/openmc
pushd openmc
mkdir build && pushd build
cmake -DCMAKE_INSTALL_PREFIX=$OPENMC_INSTALL_DIR \
      -DDAGMC_DIR=$DAGMC_INSTALL_DIR \
      -DOPENMC_USE_DAGMC=ON \
      ..
make 2>/dev/null -j2 install
popd
pip install -e .[test,docs,vtk]
popd

# Download and set cross section data
mkdir $HOME/data
pushd data
wget --content-disposition https://anl.box.com/shared/static/9igk353zpy8fn9ttvtrqgzvw1vtejoz6.xz
tar xvf endfb71.tar.xz
rm endfb71.tar.xz
popd

# Set environment variables in .profile
cat >> $HOME/.profile <<EOF
export PATH=\$HOME/openmc/build/bin:$DAGMC_INSTALL_DIR/bin:$MOAB_INSTALL_DIR/bin:$NJOY_INSTALL_DIR/bin:\$PATH
export OPENMC_CROSS_SECTIONS=\$HOME/data/endfb71_hdf5/cross_sections.xml
EOF

# Add 'source venv' in .bashrc
cat >> $HOME/.bashrc <<EOF
source \$HOME/venv/openmc_env/bin/activate
EOF

# Clone NEA course repository (requires SSH key added to openmc-nea-course github account)
ssh-keyscan github.com >> $HOME/.ssh/known_hosts
git clone git@github.com:openmc-dev/openmc-nea-course.git

# Create run_jupyter.sh
cat >> $HOME/run_jupyter.sh <<EOF
#!/bin/bash
cd openmc-nea-course
jupyter lab >\$HOME/notebook_launch.log 2>&1 &
EOF
chmod +x $HOME/run_jupyter.sh

# Install and setup Jupyter lab
pip install jupyterlab
jupyter server --generate-config
