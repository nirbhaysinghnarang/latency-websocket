Experimental WS wrapper in Python that
* Maintains a sliding window of latency measurements
* If we detect bad latency, switches over to a healthcheck endpoint (here, you would likely enable `offline mode` for your use case)
* When in the healthcheck, attempts to connect to an endpoint and benchmarks latency until it is `good enough` (here, you would likely switch back over to `online mode`)