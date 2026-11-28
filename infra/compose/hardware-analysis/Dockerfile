FROM python:3.13-slim
RUN pip install --no-cache-dir duckdb==1.5.5
WORKDIR /app
COPY tools/thinkpad_fan/hardware_analysis.py /app/hardware_analysis.py
COPY tools/thinkpad_fan/hardware_analysis.sql /app/hardware_analysis.sql
ENTRYPOINT ["python", "/app/hardware_analysis.py"]
