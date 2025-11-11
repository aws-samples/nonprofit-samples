FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

COPY requirements.txt ./

# Install dependencies (including strands-agents)
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy agent file
COPY agent.py ./
COPY system_prompt.md ./

# Expose port
EXPOSE 8080

ENV aws_region="us-east-1"
ENV llm_model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0"
ENV bucket_name="YOUR-S3-BUCKET-GOES-HERE"

ENV OTEL_PYTHON_DISTRO="aws_distro" 
ENV OTEL_PYTHON_CONFIGURATOR="aws_configurator" 

# Run application
CMD ["python3", "agent.py"]