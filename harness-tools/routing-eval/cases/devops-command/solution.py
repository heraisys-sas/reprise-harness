def command(config):
    return "docker compose -p " + config["project"] + " up -d " + config["service"]
