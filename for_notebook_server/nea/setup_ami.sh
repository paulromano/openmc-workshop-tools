#!/usr/bin/env bash
# Run as ubuntu on an Ubuntu 26.04 amd64 builder. See README.md.
set -euo pipefail
trap 'printf "NEA setup failed at line %s. Build files: %s\n" "$LINENO" "${BUILD_DIR:-not created}" >&2' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
NEA_ROOT=${NEA_ROOT:-/opt/nea}
SOFTWARE_DIR="$NEA_ROOT/software"
DATA_DIR="$NEA_ROOT/data"
VENV="$NEA_ROOT/venv"
COURSE_DIR=${COURSE_DIR:-$HOME/openmc-nea-course}
COURSE_REPO=${COURSE_REPO:-git@github.com:openmc-dev/openmc-nea-course.git}
COURSE_BRANCH=${COURSE_BRANCH:-inperson-2026-11}
PYTHON_VERSION=${PYTHON_VERSION:-3.14}
OPENMC_REF=${OPENMC_REF:-v0.16.0}
MOAB_REF=${MOAB_REF:-5.5.1}
DOUBLE_DOWN_REF=${DOUBLE_DOWN_REF:-v1.1.0}
DAGMC_REF=${DAGMC_REF:-v3.2.4}
LIBMESH_REF=${LIBMESH_REF:-v1.8.4}
NJOY_REF=${NJOY_REF:-2016.80}
OPENMC_USE_MPI=${OPENMC_USE_MPI:-OFF}
JOBS=${JOBS:-2}
JUPYTER_PORT=${JUPYTER_PORT:-8888}
START_JUPYTER=${START_JUPYTER:-1}
JUPYTER_ALLOW_ORIGIN=${JUPYTER_ALLOW_ORIGIN:-'*'}
CROSS_SECTIONS_URL=${CROSS_SECTIONS_URL:-https://anl.box.com/shared/static/9igk353zpy8fn9ttvtrqgzvw1vtejoz6.xz}
CHAIN_URL=${CHAIN_URL:-https://anl.box.com/shared/static/os1u896bwsbopurpgas72bi6aij2zzdc.xml}
# The official thermal VII.1 chain matches the chain on the inspected course AMIs.
CHAIN_SHA256=${CHAIN_SHA256:-d3981ad9d8f51136d81492ac71fbd9fc719e9502480986f73c340a8852dfbd92}

if [[ ${1:-} == --help ]]; then
    sed -n '1,190p' "$SCRIPT_DIR/README.md"
    exit 0
fi
[[ $# == 0 ]] || { echo 'Use environment variables for options; see --help.' >&2; exit 1; }
[[ $EUID != 0 ]] || { echo 'Run as the ubuntu user, with sudo available.' >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 ]] || { echo 'Ubuntu 26.04 is required.' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'This recipe is for amd64 instances.' >&2; exit 1; }
grep -qw avx2 /proc/cpuinfo || { echo 'Double-Down requires an AVX2-capable instance.' >&2; exit 1; }
[[ $OPENMC_USE_MPI == ON || $OPENMC_USE_MPI == OFF ]] || { echo 'OPENMC_USE_MPI must be ON or OFF.' >&2; exit 1; }
[[ $JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer.' >&2; exit 1; }
[[ $START_JUPYTER == 0 || $START_JUPYTER == 1 ]] || { echo 'START_JUPYTER must be 0 or 1.' >&2; exit 1; }
for path in "$HOME" "$NEA_ROOT" "$COURSE_DIR"; do
    [[ $path == /* && $path =~ ^[-A-Za-z0-9_./:+]+$ ]] || { echo 'Installation paths must be absolute and use letters, numbers, /, ., _, -, +, or :.' >&2; exit 1; }
done
# Check command execution: sudo-rs can require a password for -v even when
# the EC2 user's commands are covered by a NOPASSWD rule.
sudo true
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
packages=(build-essential gcc g++ gfortran cmake git curl ca-certificates
    "python$PYTHON_VERSION" "python$PYTHON_VERSION-venv" "python$PYTHON_VERSION-dev"
    pkg-config autoconf automake libtool m4 patch patchelf xz-utils
    libpng-dev libpugixml-dev libfmt-dev libeigen3-dev libnetcdf-dev
    libtbb-dev libembree-dev zlib1g-dev libcurl4-openssl-dev
    libgl1 libegl1 libegl-mesa0 libgl1-mesa-dri libopengl0 libxrender1 libxext6 fonts-dejavu-core)
if [[ $OPENMC_USE_MPI == ON ]]; then
    packages+=(mpich libmpich-dev libhdf5-mpich-dev)
    HDF5_ROOT=/usr/lib/x86_64-linux-gnu/hdf5/mpich
else
    packages+=(libhdf5-dev)
    HDF5_ROOT=/usr/lib/x86_64-linux-gnu/hdf5/serial
fi
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
sudo install -d -o "$(id -u)" -g "$(id -g)" "$NEA_ROOT" "$SOFTWARE_DIR" "$DATA_DIR"
BUILD_DIR=$(mktemp -d "$NEA_ROOT/build.XXXXXXXX")
RECORD_DIR="$NEA_ROOT/build-records"
mkdir -p "$RECORD_DIR"
exec > >(tee "$BUILD_DIR/setup.log") 2>&1

# Check course access before compiling. A pre-copied, clean checkout also works.
if [[ -d $COURSE_DIR/.git ]]; then
    [[ -z $(git -C "$COURSE_DIR" status --porcelain) ]] || { echo 'Course checkout has local changes; preserve them before provisioning.' >&2; exit 1; }
    git -C "$COURSE_DIR" fetch origin "$COURSE_BRANCH"
    git -C "$COURSE_DIR" checkout "$COURSE_BRANCH"
    git -C "$COURSE_DIR" merge --ff-only FETCH_HEAD
else
    git clone --single-branch --branch "$COURSE_BRANCH" "$COURSE_REPO" "$COURSE_DIR"
fi

"python$PYTHON_VERSION" -m venv "$VENV"
PYTHON="$VENV/bin/python"
"$PYTHON" -m pip install --upgrade pip setuptools wheel
# Require wheels for the large compiled Python dependencies.
"$PYTHON" -m pip install --only-binary=numpy,scipy,pandas,matplotlib,h5py,vtk \
    -r "$SCRIPT_DIR/requirements.txt"
# Configure authentication before the lengthy native builds and data download.
if [[ -z ${JUPYTER_PASSWORD_HASH:-} ]] && \
        ! "$PYTHON" "$SCRIPT_DIR/nea_environment.py" password-configured --home "$HOME"; then
    [[ -t 0 ]] || { echo 'Set JUPYTER_PASSWORD_HASH for unattended provisioning.' >&2; exit 1; }
    JUPYTER_CONFIG_DIR="$HOME/.jupyter" "$VENV/bin/jupyter" server password
fi

export CC=gcc CXX=g++ FC=gfortran
export PKG_CONFIG_PATH="$SOFTWARE_DIR/libmesh/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
PREFIX_PATH="$SOFTWARE_DIR/moab;$SOFTWARE_DIR/double-down;$SOFTWARE_DIR/dagmc;$SOFTWARE_DIR/libmesh"
LIBRARY_PATHS="$SOFTWARE_DIR/openmc/lib;$SOFTWARE_DIR/moab/lib;$SOFTWARE_DIR/double-down/lib;$SOFTWARE_DIR/dagmc/lib;$SOFTWARE_DIR/libmesh/lib"
cmake_options=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_LIBDIR=lib
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON
    "-DCMAKE_INSTALL_RPATH=$LIBRARY_PATHS" "-DCMAKE_PREFIX_PATH=$PREFIX_PATH"
    -DCMAKE_C_FLAGS=-mtune=generic -DCMAKE_CXX_FLAGS=-mtune=generic)
if [[ $OPENMC_USE_MPI == ON ]]; then
    cmake_options+=(-DMPI_C_COMPILER=/usr/bin/mpicc.mpich
        -DMPI_CXX_COMPILER=/usr/bin/mpicxx.mpich
        -DMPIEXEC_EXECUTABLE=/usr/bin/mpiexec.mpich
        -DHDF5_C_COMPILER_EXECUTABLE=/usr/bin/h5pcc.mpich)
fi

clone_source() {
    local name=$1 url=$2 ref=$3
    git clone --branch "$ref" --single-branch --depth 1 --recurse-submodules \
        --shallow-submodules "$url" "$BUILD_DIR/$name"
    printf '%s %s %s\n' "$name" "$ref" "$(git -C "$BUILD_DIR/$name" rev-parse HEAD)" >> "$BUILD_DIR/revisions.txt"
}
build_cmake() {
    local name=$1
    shift
    cmake -S "$BUILD_DIR/$name" -B "$BUILD_DIR/$name/build" "${cmake_options[@]}" "$@"
    cmake --build "$BUILD_DIR/$name/build" --parallel "$JOBS"
    cmake --install "$BUILD_DIR/$name/build"
    cp "$BUILD_DIR/$name/build/CMakeCache.txt" "$RECORD_DIR/$name-CMakeCache.txt"
}

clone_source moab https://bitbucket.org/fathomteam/moab "$MOAB_REF"
git -C "$BUILD_DIR/moab" apply "$SCRIPT_DIR/moab_endian.patch"
build_cmake moab "-DCMAKE_INSTALL_PREFIX=$SOFTWARE_DIR/moab" \
    -DBUILD_SHARED_LIBS=ON -DENABLE_HDF5=ON -DENABLE_NETCDF=ON \
    -DENABLE_FORTRAN=OFF -DENABLE_BLASLAPACK=OFF -DENABLE_PYMOAB=OFF \
    "-DHDF5_ROOT=$HDF5_ROOT" "-DHDF5_PREFER_PARALLEL=$OPENMC_USE_MPI"

clone_source double-down https://github.com/pshriwise/double-down "$DOUBLE_DOWN_REF"
# Keep upstream's AVX2 requirement without specializing for the builder's CPU.
"$PYTHON" - "$BUILD_DIR/double-down/CMakeLists.txt" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace('-march=native', '-mtune=generic'))
PY
build_cmake double-down "-DCMAKE_INSTALL_PREFIX=$SOFTWARE_DIR/double-down" \
    "-DMOAB_DIR=$SOFTWARE_DIR/moab" -DEMBREE_DIR=/usr

clone_source dagmc https://github.com/svalinn/DAGMC "$DAGMC_REF"
build_cmake dagmc "-DCMAKE_INSTALL_PREFIX=$SOFTWARE_DIR/dagmc" \
    "-DMOAB_DIR=$SOFTWARE_DIR/moab" "-DDOUBLE_DOWN_DIR=$SOFTWARE_DIR/double-down" \
    -DDOUBLE_DOWN=ON -DBUILD_TALLY=ON -DBUILD_SHARED_LIBS=ON \
    -DBUILD_STATIC_LIBS=OFF -DBUILD_STATIC_EXE=OFF -DBUILD_RPATH=ON

clone_source libmesh https://github.com/libmesh/libmesh "$LIBMESH_REF"
mkdir "$BUILD_DIR/libmesh/build"
(
    cd "$BUILD_DIR/libmesh/build"
    METHODS=opt CXXFLAGS='-O2 -g -std=gnu++23 -mtune=generic' \
        ../configure "--prefix=$SOFTWARE_DIR/libmesh" --enable-exodus \
        --enable-netcdf=v492 --disable-eigen --disable-lapack --disable-mpi
    make -j "$JOBS"
    make install
)
cp "$BUILD_DIR/libmesh/build/config.log" "$RECORD_DIR/libmesh-config.log"
# Find the installed libngcore.so beside libnglib.so instead of in the build tree.
patchelf --set-rpath "\$ORIGIN" "$SOFTWARE_DIR/libmesh/lib/libnglib.so"

clone_source njoy2016 https://github.com/njoy/NJOY2016 "$NJOY_REF"
cmake -S "$BUILD_DIR/njoy2016" -B "$BUILD_DIR/njoy2016/build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_Fortran_FLAGS=-fallow-argument-mismatch \
    "-DPython3_EXECUTABLE=$PYTHON"
cmake --build "$BUILD_DIR/njoy2016/build" --parallel "$JOBS"
# Current NJOY2016 supplies an executable without a CMake install rule.
install -d "$SOFTWARE_DIR/njoy2016/bin"
install -m 755 "$BUILD_DIR/njoy2016/build/njoy" "$SOFTWARE_DIR/njoy2016/bin/njoy"

clone_source openmc https://github.com/openmc-dev/openmc "$OPENMC_REF"
build_cmake openmc "-DCMAKE_INSTALL_PREFIX=$SOFTWARE_DIR/openmc" \
    -DOPENMC_USE_DAGMC=ON -DOPENMC_USE_LIBMESH=ON -DOPENMC_USE_OPENMP=ON \
    "-DOPENMC_USE_MPI=$OPENMC_USE_MPI" "-DHDF5_PREFER_PARALLEL=$OPENMC_USE_MPI" \
    "-DHDF5_ROOT=$HDF5_ROOT" -DOPENMC_BUILD_TESTS=OFF
# Use the installed library: CMake rewrites RUNPATH during installation, after
# its post-build copy into the source package. Package the final deployed copy.
install -m 755 "$SOFTWARE_DIR/openmc/lib/libopenmc.so" "$BUILD_DIR/openmc/openmc/lib/libopenmc.so"
"$PYTHON" -m pip install "$BUILD_DIR/openmc"
"$PYTHON" -m pip install git+https://github.com/openmc-dev/openmc_mcnp_adapter.git
if [[ $OPENMC_USE_MPI == ON ]]; then
    MPICC=/usr/bin/mpicc.mpich "$PYTHON" -m pip install --no-binary=mpi4py mpi4py
fi
"$PYTHON" -m pip check

# Preserve the course's ENDF/B-VII.1 library. Allow an existing copy to be supplied.
CROSS_SECTIONS=${CROSS_SECTIONS:-}
if [[ -z $CROSS_SECTIONS ]]; then
    mapfile -t existing_xml < <(find "$DATA_DIR" -name cross_sections.xml -type f)
    if [[ ${#existing_xml[@]} == 0 ]]; then
        curl --fail --location --retry 3 "$CROSS_SECTIONS_URL" -o "$BUILD_DIR/endfb71.tar.xz"
        tar -xJf "$BUILD_DIR/endfb71.tar.xz" --no-same-owner -C "$DATA_DIR"
        sha256sum "$BUILD_DIR/endfb71.tar.xz" > "$RECORD_DIR/cross-sections-archive.sha256"
        mapfile -t existing_xml < <(find "$DATA_DIR" -name cross_sections.xml -type f)
    fi
    [[ ${#existing_xml[@]} == 1 ]] || { echo 'Set CROSS_SECTIONS to the desired cross_sections.xml.' >&2; exit 1; }
    CROSS_SECTIONS=${existing_xml[0]}
fi
CHAIN_FILE="$DATA_DIR/depletion_chains/chain_endfb71_pwr.xml"
mkdir -p "$(dirname "$CHAIN_FILE")"
if [[ ! -s $CHAIN_FILE ]]; then
    curl --fail --location --retry 3 "$CHAIN_URL" -o "$CHAIN_FILE.part"
    printf '%s  %s\n' "$CHAIN_SHA256" "$CHAIN_FILE.part" | sha256sum --check -
    mv "$CHAIN_FILE.part" "$CHAIN_FILE"
fi
printf '%s  %s\n' "$CHAIN_SHA256" "$CHAIN_FILE" | sha256sum --check -

"$PYTHON" -m ipykernel install --sys-prefix --name openmc-nea --display-name 'Python (OpenMC NEA)'
"$PYTHON" "$SCRIPT_DIR/nea_environment.py" configure \
    --home "$HOME" --root "$NEA_ROOT" --course "$COURSE_DIR" \
    --cross-sections "$CROSS_SECTIONS" --chain "$CHAIN_FILE" \
    --port "$JUPYTER_PORT" --allow-origin "$JUPYTER_ALLOW_ORIGIN" \
    --user "$(id -un)" --unit-file "$BUILD_DIR/nea-jupyter.service"
# Hashes can be supplied through JUPYTER_PASSWORD_HASH for unattended builds.
if ! "$PYTHON" "$SCRIPT_DIR/nea_environment.py" password-configured --home "$HOME"; then
    echo "Configure a Jupyter password using $VENV/bin/jupyter server password." >&2
    exit 1
fi
install -m 755 "$SCRIPT_DIR/nea_environment.py" "$NEA_ROOT/nea_environment.py"
"$PYTHON" -m pip freeze > "$RECORD_DIR/python-requirements.txt"
dpkg-query -W > "$RECORD_DIR/system-packages.txt"
cp /etc/os-release "$RECORD_DIR/os-release"
"$PYTHON" --version > "$RECORD_DIR/python-version.txt"
sha256sum "$CHAIN_FILE" > "$RECORD_DIR/chain.sha256"
cp "$BUILD_DIR/revisions.txt" "$RECORD_DIR/revisions.txt"
printf 'course %s %s\n' "$COURSE_BRANCH" "$(git -C "$COURSE_DIR" rev-parse HEAD)" >> "$RECORD_DIR/revisions.txt"
install -m 644 "$SCRIPT_DIR/setup_ami.sh" "$SCRIPT_DIR/requirements.txt" "$SCRIPT_DIR/moab_endian.patch" "$RECORD_DIR/"

# Verify both executable and in-memory transport before enabling the service.
set -a
# shellcheck source=/dev/null
source "$HOME/.config/openmc-nea/environment"
set +a
"$PYTHON" "$NEA_ROOT/nea_environment.py" verify --root "$NEA_ROOT" --transport
"$SOFTWARE_DIR/openmc/bin/openmc" --version > "$RECORD_DIR/openmc-version.txt"
# Repair logs made by the older service, which opened them as root.
if [[ -f $HOME/notebook_launch.log ]]; then
    sudo chown "$(id -u):$(id -g)" "$HOME/notebook_launch.log"
    chmod 600 "$HOME/notebook_launch.log"
fi
sudo install -m 644 "$BUILD_DIR/nea-jupyter.service" /etc/systemd/system/nea-jupyter.service
sudo systemctl daemon-reload
sudo systemctl enable nea-jupyter.service
if [[ $START_JUPYTER == 1 ]]; then
    sudo systemctl restart nea-jupyter.service
    for ((attempt=0; attempt<30; attempt++)); do
        if curl --fail --silent "http://127.0.0.1:$JUPYTER_PORT/login" > /dev/null; then
            break
        fi
        sleep 1
    done
    curl --fail --silent "http://127.0.0.1:$JUPYTER_PORT/login" > /dev/null
fi
echo "NEA environment is ready. Build records: $RECORD_DIR"
echo "Inspect $BUILD_DIR and the course notebooks before creating the AMI."
echo 'Jupyter logs: sudo journalctl -u nea-jupyter.service (and ~/notebook_launch.log).'
