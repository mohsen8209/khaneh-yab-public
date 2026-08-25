import os


# This entrypoint is exclusively for the hosted PaaS process.
os.environ["KHANEYAB_RUNTIME"] = "paas"

from divar_scout.runtime import run_paas  # noqa: E402


if __name__ == "__main__":
    run_paas()
