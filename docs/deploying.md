# Deploying GeneTrek

GeneTrek is one Python process: FastAPI with NiceGUI mounted at `/`, served by a single
uvicorn worker. It reads a resources tree (the parquet, its YAML metadata and the
configuration) at start-up and needs no database server.

## Container image

```bash
docker build -t genetrek .
docker run --rm -p 8000:8000 genetrek          # http://localhost:8000
```

The image is built from `uv.lock`, runs as an unprivileged user (uid 1000) and listens on
port 8000. `/api/health` answers once the data is loaded; use it for probes.

Things that matter in production:

- **One worker, one replica.** Each browser session's state lives in the process that served
  it, so a second worker or replica would answer a reconnect from a process that never saw
  that client. Scaling out would need NiceGUI's Redis storage and session affinity, which are
  not tested.
- **Memory.** Steady state is about 210 MB resident, but serialising the whole database for a
  download peaks around 570 MB. Give the container at least 1.5 GiB.
- **Environment.**
  - `GENETREK_RESOURCES` points at the resources tree (`/app/resources` in the image). Mount
    another tree there to serve different data without rebuilding.
  - `POLARS_MAX_THREADS=2` and `MALLOC_ARENA_MAX=2` keep threads and memory in line with a
    one-core limit.
- **Companion files.** `resources/static/` is served at `/data`. The public instance adds the
  Rolland et al. 2023 variant files and its UCSC track hub there at deploy time; they are not
  in this repository, and without them `/data` and `/downloadVCF` answer 404.
- **Behind a proxy.** The image trusts `X-Forwarded-*` headers from any peer, which is safe
  only when nothing but the reverse proxy or ingress can reach port 8000.

## Kubernetes (Helm)

`chart/` deploys the image with a Deployment, a Service and an optional Ingress. Its
defaults are generic; supply a values file for your cluster:

```yaml
image:
  repository: registry.example.org/genetrek
  tag: v2.0.0
ingress:
  enabled: true
  className: nginx            # empty uses the cluster default
  host: genetrek.example.org
  tlsSecretName: genetrek-tls # an existing secret; empty serves no TLS block
```

```bash
helm upgrade --install genetrek ./chart -n genetrek -f my-values.yaml \
  --set-string image.tag=v2.0.0 --wait
```

Pass the tag with `--set-string`: an all-digit commit sha would otherwise become a number.
The chart's resource requests and limits, probes and environment follow the sizing above.

With an Ingress host, the chart also sets `GENETREK_PUBLIC_URL=https://<host>`. Link
previews (the card Slack, Teams, LinkedIn or X draw for a pasted link) need the absolute
address of the page and of its image, `/link-preview.png`; without it the pages carry a
title and description only. Outside the chart, set it yourself to the address visitors use.

### Visit counting (optional)

`goatcounter.enabled: true` adds a self-hosted [GoatCounter](https://www.goatcounter.com)
beside the application: a Deployment (one replica, `Recreate`), a Service, a 1 GiB
ReadWriteOnce PersistentVolumeClaim for its SQLite database (kept by `helm uninstall`),
and a `<basePath>/count` path on the application's own Ingress. The pages then report each
load to it, with the page's path and nothing else (`src/genetrek_server/visits.py`).

```yaml
goatcounter:
  enabled: true
  image:
    repository: registry.example.org/mirror/goatcounter   # default: docker.io/arp242/goatcounter
  dashboard:
    className: internal        # a class only your own network can reach
    host: stats.example.internal
```

Without `dashboard.host` the dashboard has no Ingress; reach it with
`kubectl port-forward svc/<name>-goatcounter 8080` and `http://localhost:8080/stats/`.

Create the site and its first user before the dashboard is reachable: an instance with no
site offers a "create the first account" form to whoever opens it first.

```bash
kubectl exec -it deploy/<name>-goatcounter -- goatcounter db create site \
  -db sqlite+/home/goatcounter/goatcounter-data/db.sqlite3 \
  -vhost <your host> -user.email <you>
```

Then, in the dashboard's site settings under "Collect", keep **Sessions** and **Country**
and turn the rest off: the pages send no referrer, title or screen size, and with
"Individual pageviews" off only counts are stored. GoatCounter reads the client address
from `X-Real-Ip` or `X-Forwarded-For` and ignores private addresses; check that the
country it records is the visitor's and not your proxy's: logged in, open
`<basePath>/settings/main/ip`, which shows the address GoatCounter sees for you.
