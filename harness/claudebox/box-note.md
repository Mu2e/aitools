
## Running in a sandbox (claudebox)

You are in an apptainer container on one of the shared machines. The
home-quota remarks and the `/exp` paths above do not apply: only the paths
listed under "This session" exist. The best practices for shared machines do
apply: the container uses this machine's cores and memory. There is no Kerberos (no `kinit`, no
`ssh`) and the host environment is not visible. Write results in the work
directory. The harness guard-rail hook is not active here; the container
is the guard.
