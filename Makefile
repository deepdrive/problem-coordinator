.PHONY: build push run bash test deploy reboot_vm prepare

TAG=gcr.io/silken-impulse-217423/deepdrive-problem-coordinator
SSH=gcloud compute ssh deepdrive-problem-coordinator
SERVER_CONTAINER_NAME= klt-deepdrive-problem-coordinator-lnaf

build:
	docker build -t $(TAG) .

push:
	docker push $(TAG)

#test: build
#	docker run -it $(TAG) bin/test.sh

RUN_ARGS=-v ~/.gcpcreds/:/root/.gcpcreds --init
RUN_ARGS_DEV=$(RUN_ARGS) --net=host -e GOOGLE_APPLICATION_CREDENTIALS=/root/.gcpcreds/VoyageProject-d33af8724280.json

ssh:
	$(SSH)

prepare:
	$(SSH) --command "sudo docker image prune -f"
	$(SSH) --command "sudo docker container prune -f"
	$(SSH) --command "sudo docker stop $(SERVER_CONTAINER_NAME) || echo Perhaps no container"

reboot_vm:
	$(SSH) --command "echo connection successful"
	$(SSH) --command "sudo reboot" || echo "\e[1;30;48;5;82m SUCCESS \e[0m Error above is due to reboot. You'll be able to run 'make ssh' again in a few seconds."

just_deploy: push prepare reboot_vm

deploy: build local_test push prepare reboot_vm

local_test:
	python test/test.py

test:
	docker run $(RUN_ARGS_DEV) -it $(TAG) python test/test.py

# GCE runs the container via args configured in the instance, not here!
local_run:
	docker run $(RUN_ARGS) --restart=unless-stopped --detach -e LOGURU_LEVEL=INFO $(TAG)

devrun:
	docker run $(RUN_ARGS_DEV) -it $(TAG)

bash:
	docker run -it $(TAG) bash

logs:
	$(SSH) --command "docker logs $(SERVER_CONTAINER_NAME) --follow --tail 100"

start:
	# Use this if you've taken the semaphore locally
	$(SSH) --command "docker start $(SERVER_CONTAINER_NAME)"
