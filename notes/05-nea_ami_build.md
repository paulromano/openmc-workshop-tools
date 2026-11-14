> Created: 2026-11-14; Producer: Codex / GPT-6.1-Sol high

# Build an Ubuntu 26.04 AMI for the NEA OpenMC course

The workflow is to launch a vanilla Ubuntu builder, run the course setup, test it, create an AMI from its disk, and test a fresh instance launched from that AMI. The setup script provisions the builder; creating the AMI is a separate EC2 operation.

Commands labeled **local** run on your own computer from the root of `openmc-workshop-tools`. Commands labeled **builder** run on the EC2 instance as `ubuntu`. Replace `HOST` with the builder's public DNS name or IP address.

## 1. Launch the builder

**Local:** check launch permissions, then launch one instance:

```bash
python scripts/launch_ubuntu_instance.py nea-build --dry-run
python scripts/launch_ubuntu_instance.py nea-build
```

The [launcher](../scripts/launch_ubuntu_instance.py) resolves Canonical's current stable Ubuntu Server 26.04 amd64 image through SSM, verifies the publisher, and uses these defaults:

| Setting | Default |
| --- | --- |
| Region | `us-east-1` |
| Instance type | `c7i-flex.large` |
| Root disk | 64 GiB, gp3 |
| Key pair | `X1 Carbon 2022` |
| Security group | `sg-130bcb6b` |
| Instance Name and `ws_group` | `nea-build` |

Use a unique group name if you want to keep multiple builders separate. The group works with `scripts/get_ip_addresses.py` and `scripts/terminate_instances.py`. The launcher prints the instance ID, addresses, and SSH command. SSH and cloud-init may still be initializing when the instance first reaches the running state.

For a 32-GiB root volume, launch with:

```bash
python scripts/launch_ubuntu_instance.py nea-build --root-volume-gb 32
```

Allow at least 32 GiB; 64 GiB gives more room for retained build files. Instances created from the resulting AMI inherit its root-volume size as their minimum, so choose that size with the course workers in mind. A builder with more RAM can help compile libMesh; the setup defaults to two build jobs.

For a console launch, select Canonical's Ubuntu Server 26.04 **amd64** image in `us-east-1`. In **Configure storage**, change the existing root volume from 8 GiB to the desired size and keep gp3. The script requires an amd64 CPU with AVX2 support. Keep SSH accessible to your administrative connection and port 8888 accessible to the trusted course proxy. See [Canonical's image lookup instructions](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/).

## 2. Copy the recipe and forward GitHub authentication

**Local:** copy the entire directory, including its Python helper, requirements, and MOAB patch:

```bash
scp -r for_notebook_server/nea ubuntu@HOST:~/nea-ami
```

The course repository is private. Load a GitHub-authorized key into your local SSH agent if it is not already loaded:

```bash
ssh-add -l
ssh-add /path/to/your/github/private-key
```

Connect with agent forwarding:

```bash
ssh -A ubuntu@HOST
```

Agent forwarding lets the builder authenticate to GitHub through your local agent while keeping the private key on your computer. The EC2 login key and the GitHub key can be different. See [GitHub's agent-forwarding instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/using-ssh-agent-forwarding).

## 3. Run the setup in tmux

**Builder:** install Git and tmux, then open a persistent terminal session:

```bash
sudo apt-get update
sudo apt-get install -y git tmux
tmux new -s nea-build
```

Inside tmux, verify that GitHub authentication and access to the selected branch work:

```bash
git ls-remote --exit-code \
    git@github.com:openmc-dev/openmc-nea-course.git \
    refs/heads/inperson-2026-11
```

The command should print the branch's commit hash. On the first GitHub connection, verify the SSH host key when prompted. If access fails, fix SSH-agent forwarding or repository access before running the build. A clean existing checkout at `~/openmc-nea-course` also needs working authentication because the setup fetches the selected branch.

Run the setup as `ubuntu`:

```bash
cd ~/nea-ami
bash setup_ami.sh
```

The defaults are Python 3.14, OpenMC 0.16.0, the updated native dependencies, and the `inperson-2026-11` course branch. The script prompts for the Jupyter password after installing the Python packages. Its Jupyter service is enabled at boot and started at the end of a successful setup. See the [recipe README](../for_notebook_server/nea/README.md) for version pins and options.

Detach from tmux with **Ctrl+B**, then **D**. To reconnect, use `ssh -A ubuntu@HOST`, then `tmux attach -t nea-build`. The forwarded SSH agent belongs to the SSH connection; after reconnecting, check GitHub access again before retrying any private-repository operations inside an older tmux shell.

Build output is saved in `/opt/nea/build.*/setup.log`. The script stops at an error and reports its build directory. Rerunning it creates a new build directory and rebuilds the native components.

If an older copy prompts for a sudo password at the initial `sudo -v`, press **Ctrl+C**. Ubuntu 26.04's sudo-rs can request authentication for validation even when commands have passwordless sudo permission. The current script uses `sudo true`; update the old copy with:

```bash
sed -i 's/^sudo -v$/sudo true/' setup_ami.sh
bash setup_ami.sh
```

The Jupyter password prompt later in the setup is separate from sudo authentication.

## 4. Test the builder and reboot

**Builder:** after the setup finishes, inspect the service and rerun the installed checks from a fresh login shell:

```bash
sudo systemctl status nea-jupyter.service
/opt/nea/venv/bin/python /opt/nea/nea_environment.py verify \
    --root /opt/nea --transport
```

The verification checks imports, native build features, matching executable/Python libraries, nuclear data and the depletion chain, NJOY, VTK rendering, executable transport, and in-memory transport. Read notebook-server output with:

```bash
tail -f ~/notebook_launch.log
```

To test browser access over SSH, run this in a separate **local** terminal and keep it connected:

```bash
ssh -L 8889:127.0.0.1:8888 ubuntu@HOST
```

Open `http://localhost:8889` and sign in with the Jupyter password chosen during setup. Also test through the normal course proxy before creating the image; the [proxy notes](02-proxy_server.txt) describe that configuration.

Run representative course notebooks, especially CAD geometry, unstructured mesh tallies, depletion, shutdown dose, and visualization. Confirm that the kernels use the new course environment. The software is newer than the 2025 course environment, so notebook compatibility must be checked on the builder.

**Builder:** reboot after the package upgrades and completed build:

```bash
sudo reboot
```

Reconnect and repeat the service, environment, and browser checks. Jupyter should start automatically. Its configuration is in `~/.jupyter`, and the shared shell/service environment is in `~/.config/openmc-nea/environment`. The generated `~/run_jupyter.sh` calls `sudo systemctl start nea-jupyter.service`; repeated starts leave an already-running service alone.

## 5. Prepare the disk for the AMI

**Builder:** finish the notebook checks, preserve the build records, and remove disposable build files, notebook test outputs, and any private credentials that should not be distributed. `/opt/nea/build.*` contains the retained build directories; inspect those directories before removing them. Keep `/opt/nea/build-records`, `/opt/nea/software`, `/opt/nea/venv`, `/opt/nea/data`, the course checkout, and the generated configuration files.

Stop Jupyter, clear its temporary connection files and cookie secret, reset cloud-init state, and shut down:

```bash
sudo systemctl stop nea-jupyter.service
jupyter_runtime_dir=$(/opt/nea/venv/bin/jupyter --runtime-dir)
find "$jupyter_runtime_dir" -maxdepth 1 -type f -delete
sudo cloud-init clean --logs --machine-id
sudo shutdown -h now
```

Leave the Jupyter service enabled so that it starts on new instances. Keep its password configuration in `~/.jupyter/jupyter_server_config.json`; the cleanup above clears runtime files. The cloud-init cleanup allows the next boot to initialize fresh instance state and a unique machine ID. See [cloud-init's cleanup documentation](https://docs.cloud-init.io/en/latest/reference/cli.html#clean).

After this cleanup, keep the builder stopped until its image has been created. Rebooting the builder would rerun cloud-init and recreate runtime state.

## 6. Create the AMI

In the EC2 console in `us-east-1`, select the stopped builder and choose **Actions → Image and templates → Create image**. Use a name such as `OpenMC NEA Course 2026-11 v1` and a description recording the OS, OpenMC version, and course branch.

Suggested image and snapshot tags are:

| Key | Value |
| --- | --- |
| `UbuntuVersion` | `26.04` |
| `PythonVersion` | `3.14` |
| `OpenMCVersion` | `0.16.0` |
| `CourseBranch` | `inperson-2026-11` |

Creating the image takes snapshots of the instance's EBS volumes and registers a new AMI. Wait for the AMI state to become **Available** and record its AMI ID. See [AWS's AMI creation instructions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/creating-an-ami-ebs.html).

## 7. Test a fresh instance and update the course launcher

Launch one test instance from the new AMI in the course's network arrangement, selecting the intended SSH key pair and security group. Verify SSH access, automatic Jupyter startup, browser login, and the installed environment checks. This confirms that the image works independently of the builder's session and temporary files.

After the fresh-instance checks pass, update `IMAGE_ID` in [scripts/launch_instances.py](../scripts/launch_instances.py) to the new AMI ID. That script launches course workers; [scripts/launch_ubuntu_instance.py](../scripts/launch_ubuntu_instance.py) continues to launch vanilla Ubuntu builders.

Keep the AMI and its snapshots for the course. The builder and test instance can be terminated after validation if they are no longer needed. Terminating an instance is separate from deregistering an AMI or deleting its snapshots.
