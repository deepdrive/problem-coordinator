# Problem coordinator

Event loop that starts instances and assigns eval jobs to them for Deepdrive's
Botleague implementation.


# Deployment

This app is designed to run on one GCP machine and GCP's docker support.
Set the container restart policy to "On failure" so that the container restarts
if there's an error, but does not restart when you manually stop it.

> The config for the problem-coordinator is [here](cloud_configs/create-problem-coordinator.http). 

This allows you to stop the deployed version and start a local version to debug.
Then when you want to restart the deployed version, you can run

```
docker start <container-name>
# i.e.
docker start klt-deepdrive-problem-coordinator-lnaf
```

This will ensure only one version of the container is running. If you run 
`docker run` on the VM, you'll create another container on the VM
that will fight for the semaphore when the machine boots for example. If you 
don't want to do that, and don't mind waiting for the VM to start, you can
just stop and start the whole VM and the container will pull and start for you.


