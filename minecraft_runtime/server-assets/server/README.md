Put Paper root server config files here (for example `server.properties`, `ops.json`, `paper-global.yml`).

These files sync to:

- `s3://<bucket>/<prefix>/server/`

And then onto:

- `/opt/minecraft/server/` on the EC2 host.
