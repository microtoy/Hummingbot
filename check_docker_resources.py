import docker
try:
    client = docker.from_env()
    info = client.info()
    mem_total = info.get('MemTotal', 0)
    print(f"Docker Total Memory: {mem_total / (1024**3):.2f} GB")
except Exception as e:
    print(f"Error checking docker: {e}")
