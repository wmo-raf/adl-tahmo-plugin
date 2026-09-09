# ADL TAHMO Plugin

Collects observation data from the **[TAHMO](https://tahmo.org) Data Hub** into
an [ADL](https://github.com/wmo-raf/adl) instance. TAHMO names each measured
quantity by a variable shortcode (`te`, `rh`, `pr`, …) and stations carry
different sensor sets, so variable mappings are defined per station link. On
each collection cycle ADL asks the Data Hub for each linked station's raw
measurements over the run's window, keeps the entries the API marks as good,
and stores them against your ADL stations and data parameters.

**Operator guide:** [docs/guide.md](docs/guide.md) — prerequisites,
installation, every connection and station-link field, the metadata explorer,
collection behaviour, diagnostics and troubleshooting. The guide is also
published on the central ADL documentation site.

## Development setup

The plugin runs inside the ADL core image. Build the `adl:latest` image from
the [ADL core repository](https://github.com/wmo-raf/adl) first, then:

```bash
git clone https://github.com/wmo-raf/adl-tahmo-plugin.git
cd adl-tahmo-plugin
cp .env.sample .env        # set PLUGIN_BUILD_UID=$(id -u), PLUGIN_BUILD_GID=$(id -g), ADL_DB_PASSWORD
docker compose build
docker compose up
docker compose exec adl adl createsuperuser
```

The admin is served on `PORT` (default 8080). The plugin source is
bind-mounted, so code changes reload the dev server. If the build fails with
`pull access denied` for `adl:latest`, prefix the build with
`DOCKER_BUILDKIT=0`.

Lint and format from `plugins/adl_tahmo_plugin/` with `make lint` and
`make format`. See [CONTRIBUTING.md](CONTRIBUTING.md) — a change to any
connection or station-link field must update the guide in the same PR.
