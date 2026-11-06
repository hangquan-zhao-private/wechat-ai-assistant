FROM python:3.12-slim

# xdotool/xclip drive WeChat's X display for the Moments RPA.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xdotool xclip \
    && rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt pyperclip pillow

WORKDIR /app
CMD ["python", "-m", "agent.webapp"]
