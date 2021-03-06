# Problem coordinator

Event loop (single actor model) that starts instances and assigns eval jobs 
to them for Deepdrive's Botleague implementation.


# Deployment

This app is designed to run on one GCP machine and GCP's docker support.
Set the container restart policy to "Always" so that the container restarts
even if there's an error. This means that if you want to stop the service,
you should stop the VM, as just stopping the container can sometimes
result in an unexpected restart in my experience.

> The config for the problem-coordinator is [here](cloud_config_history/create-problem-coordinator.http). 

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

# Loop handling

If you get

```
singleton_loop:obtain_semaphore:78 - Waiting for other loop to end
```

the semaphore probably got into a bad state. You can set the 
deepdrive_eval_loop_semphore to `stopped` [here](https://console.firebase.google.com/u/1/project/silken-impulse-217423/database/firestore/data~2Fdeepdrive_eval_loop_semaphore~2Fstatus)
to get things started again.


