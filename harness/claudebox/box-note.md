
## Running in a sandbox (claudebox)

You are in an apptainer container, not directly on a gpvm. Ignore the
gpvm and home-quota remarks above, and the `/exp` paths: only the paths
listed under "This session" exist. There is no Kerberos (no `kinit`, no
`ssh`) and the host environment is not visible. Write results in the work
directory. The harness guard-rail hook is not active here; the container
is the guard.
