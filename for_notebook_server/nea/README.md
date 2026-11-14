> Created: 2026-11-13; Producer: Codex / GPT-6.1-Sol high\
> Updated: 2026-11-14; Producer: Codex / GPT-6.1-Sol high\
> Updated: 2026-11-14; Producer: Codex / GPT-6.1-Sol high

# NEA course AMI setup

This recipe provisions an Ubuntu 26.04 amd64 builder for the NEA OpenMC course. Run it as `ubuntu` with sudo access. It creates one course virtual environment, builds the native software, installs the course and nuclear data, and configures a password-protected JupyterLab service. It includes no ASV or GitHub Actions runner setup.

The recipe was assembled from the newer `openmc-performance-bench` dependency scripts and the configuration inspected on the November 2025 and May 2026 NEA images. The default course branch is `inperson-2026-11`. The older setup script in the parent directory is preserved.

## Build

Use a fresh Canonical Ubuntu 26.04 amd64 instance with an AVX2-capable CPU, such as the course's `c7i-flex.large`. Allow at least 32 GiB of root storage; 64 GiB gives more room for the retained source and build directories. `JOBS=2` is the default. More build memory can reduce the chance of compiler processes exhausting RAM when building libMesh.

Copy this entire directory, including its helper, requirements, and patch:

```bash
scp -r for_notebook_server/nea ubuntu@HOST:~/nea-ami
```

Before running the script, make the private course repository accessible through your GitHub SSH authentication and known-hosts configuration. A clean checkout already at `~/openmc-nea-course` also works; the script fetches the selected branch and permits only a fast-forward update. Preserve any local changes before provisioning.

On the builder:

```bash
cd ~/nea-ami
bash setup_ami.sh
```

An interactive build prompts for the Jupyter password after installing the Python packages, before the native builds. For an unattended build, export `JUPYTER_PASSWORD_HASH` with a hash produced by `jupyter_server.auth.passwd`. An existing password in `~/.jupyter/jupyter_server_config.json` is preserved when no replacement hash is supplied. The script does not print the password or its hash.

The script installs and upgrades Ubuntu packages. Native builds and the roughly 6-GiB extracted cross-section library take additional time and disk space. Sources, compiler output, and build logs remain under `/opt/nea/build.*` for inspection. Failed builds stop immediately and report their build directory. A rerun uses a new build directory and rebuilds the native components.

## Defaults and options

| Setting | Default |
| --- | --- |
| OS | Ubuntu 26.04, amd64 |
| Python | Ubuntu-packaged 3.14 |
| OpenMC | Latest `develop` branch at build time |
| MOAB | `5.5.1`, with PyMOAB disabled |
| Double-Down | `v1.1.0` |
| DAGMC | `v3.2.4`, with Double-Down and mesh tallies |
| libMesh | `v1.8.4`, optimized, Exodus and bundled NetCDF enabled, MPI disabled |
| Embree | Ubuntu's `libembree-dev` |
| NJOY2016 | `2016.80` |
| OpenMC features | OpenMP, DAGMC, libMesh, PNG; MPI disabled |
| Course | `inperson-2026-11` |
| Nuclear data | Existing course ENDF/B-VII.1 download and thermal VII.1 depletion chain |
| Jupyter | Port 8888, password required, course directory as the root |

Python 3.15 became stable on October 9, 2026, and current VTK packages include Python 3.15 wheels. Python 3.14 is the default here because Ubuntu 26.04 supplies it and OpenMC 0.16.0 explicitly lists support for it. See [Python's release page](https://www.python.org/downloads/release/python-3150/), [VTK packages](https://pypi.org/project/vtk/), and [OpenMC's Python requirements](https://github.com/openmc-dev/openmc/blob/v0.16.0/pyproject.toml). Setting `PYTHON_VERSION` selects another interpreter only if its Ubuntu packages are available in the builder's configured repositories; the script does not add Python repositories or compile Python.

The source versions above are configurable through `OPENMC_REF`, `MOAB_REF`, `DOUBLE_DOWN_REF`, `DAGMC_REF`, `LIBMESH_REF`, and `NJOY_REF`. They accept cloneable branches or tags. OpenMC defaults to the latest `develop` commit at build time, and `/opt/nea/build-records/revisions.txt` records the exact commit used. To build the released OpenMC 0.16.0 instead, run `OPENMC_REF=v0.16.0 bash setup_ami.sh`. Python's direct package versions are in `requirements.txt`; the completed build saves all resolved versions.

Other options are environment variables:

```bash
COURSE_BRANCH=online-2026-05 JOBS=4 bash setup_ami.sh
```

| Variable | Purpose |
| --- | --- |
| `NEA_ROOT` | Installation root; defaults to `/opt/nea`. |
| `COURSE_DIR` | Defaults to `~/openmc-nea-course`. |
| `COURSE_REPO` | Git URL for a new course checkout; defaults to the GitHub SSH URL. |
| `OPENMC_USE_MPI=ON` | Selects MPICH and parallel HDF5 for OpenMC, and builds mpi4py against MPICH. The default matches the inspected serial/OpenMP course builds. |
| `CROSS_SECTIONS` | Use an existing, persistent `cross_sections.xml`, avoiding the large download. |
| `CROSS_SECTIONS_URL` | Alternative data archive URL; verify its format and course suitability. |
| `CHAIN_URL`, `CHAIN_SHA256` | Alternative depletion-chain URL and required checksum. |
| `JUPYTER_PORT` | Defaults to 8888, as expected by `scripts/update_proxy.py`. |
| `JUPYTER_ALLOW_ORIGIN` | Defaults to `*`, matching the inspected course servers. |
| `START_JUPYTER=0` | Enable the service for future boots without starting it on the builder. |

The default chain's SHA-256 hash matches the 27,657,141-byte chain on both inspected course images. Data links come from the [OpenMC data page](https://openmc.org/data/). The script discovers the archive's actual cross-section directory and provides the older `~/data/endfb71_hdf5` path as an alias. Existing `~/data` and `~/software` directories are preserved; otherwise, those names link into `/opt/nea`.

## Environment and Jupyter

The environment lives at `/opt/nea/venv`; native software lives at `/opt/nea/software`. OpenMC is installed as a regular Python package containing the compiled shared library. Jupyter, its kernel, and the OpenMC executable use the same configured environment. Login and interactive shells load `~/.config/openmc-nea/environment`, which is also read by systemd. Nuclear-data paths, library paths, and VTK's off-screen setting are set there explicitly. Matplotlib's notebook backend is left to IPython.

Jupyter uses the existing proxy settings: it listens on all interfaces, allows remote access, trusts forwarded headers, and serves the selected course directory on port 8888. Keep instance port access restricted to the trusted course proxy and administrative connections. Password authentication uses Jupyter's normal password configuration; token and XSRF protections are not manually disabled. See [Jupyter authentication](https://jupyter-server.readthedocs.io/en/stable/operators/security.html).

The systemd service starts on boot and restarts after failures. The generated `~/run_jupyter.sh` starts the service, so the existing `scripts/start_nb_servers.py` entry point still works. Repeated starts do not create duplicate notebook servers.

```bash
sudo systemctl status nea-jupyter.service
sudo systemctl restart nea-jupyter.service
tail -f ~/notebook_launch.log
```

## Checks before creating the AMI

The setup checks Python imports, OpenMC build features, matching executable/Python shared-library hashes, the data paths and chain, NJOY, VTK file I/O and off-screen rendering, a small executable transport calculation, and an in-memory transport calculation. It also checks the Jupyter login page when the service is started. It saves source revisions, CMake configuration, package versions, and the recipe in `/opt/nea/build-records`.

Rerun the installed checks from a login shell:

```bash
/opt/nea/venv/bin/python /opt/nea/nea_environment.py verify --root /opt/nea --transport
```

Run the course notebooks through the real proxy before creating the image, especially CAD geometry, unstructured mesh tallies, depletion, shutdown dose, and visualization. OpenMC and the Python packages are newer than the 2025 course environment, so those notebook checks are part of preparing the course image. Inspect and remove build scratch files and any private Git credentials that should not be distributed. The script leaves those files available for your review.

Create the AMI using the usual EC2 image-creation process after the course checks pass. This recipe does not launch instances, create an AMI, or change the production AMI ID in `scripts/launch_instances.py`.

## Local script checks

```bash
bash -n for_notebook_server/nea/setup_ami.sh
shellcheck for_notebook_server/nea/setup_ami.sh
python -B -m unittest discover -s for_notebook_server/nea/tests -v
```

The local tests check generated environment files, legacy paths, password preservation, repeat runs, Jupyter's loading of the generated configuration, and systemd's parsing of the generated service. The Jupyter and systemd checks require those tools locally. The Ubuntu 26.04 native build and full course-notebook run must be performed on the builder before using the new AMI for a course.
