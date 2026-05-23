FROM apify/actor-python:3.13

# social-analyzer pulls a fairly heavy dependency tree (chromium / pyppeteer / langdetect etc.)
RUN pip install --no-cache-dir social-analyzer

# Verify install
RUN social-analyzer --help | head -3

COPY requirements.txt /actor/requirements.txt
RUN pip install --no-cache-dir -r /actor/requirements.txt

COPY . /actor
WORKDIR /actor

CMD ["python3", "-m", "src.main"]
