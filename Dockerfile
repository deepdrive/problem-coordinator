FROM python:3.7
RUN curl -sSL https://sdk.cloud.google.com | bash
ENV PATH="/root/google-cloud-sdk/bin:${PATH}"
RUN mkdir problem-coordinator
WORKDIR problem-coordinator
COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY . .

CMD bin/run.sh
