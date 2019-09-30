FROM python:3.7
RUN curl -sSL https://sdk.cloud.google.com | bash
ENV PATH="/root/google-cloud-sdk/bin:${PATH}"
RUN mkdir problem-coordinator
WORKDIR problem-coordinator
COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Ensure signals are sent to our python process
# c.f. https://hynek.me/articles/docker-signals/
################################################################################
STOPSIGNAL SIGINT
# GCE has no option to set `--init`, so we use Tini
# Add Tini
ENV TINI_VERSION v0.18.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod +x /tini
ENTRYPOINT ["/tini", "--"]
################################################################################

COPY . .

# Make sure we have up to date github backed libs
RUN pip install --upgrade --force-reinstall --ignore-installed --no-cache-dir git+git://github.com/botleague/botleague-helpers#egg=botleague-helpers
RUN pip install --upgrade --force-reinstall --ignore-installed --no-cache-dir git+git://github.com/deepdrive/problem-constants#egg=problem-constants

RUN ["bin/get_shared_libs.sh"]

# Don't run a shell script here or python won't receive SIGnals
CMD ["python", "-u", "coordinator.py"]
