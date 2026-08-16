# KSS Harness profile

Stacked bundles: `@deepseek-ai/dsh-base` then `@kss/harness-plugins`. Does not stack `dsh-web-app`.

Pinned upstream (developer preview): `47f943859bef60e4160492346772ded9b24f765a` of https://github.com/deepseek-ai/deepseek-harness. Runnable CLI family on npm: `@deepseek-ai/dsh@0.1.0-rc.6`.

This profile does not stack `dsh-headless`, so `dsh --profile kss` has no one-shot task app and will not create an idle agent by itself. U1 verification is `--dump-config` (no `dsh-web-app`; `id: kss` insert present). Agent spawn to idle is a later-unit / driver concern.

Dump the composed tree (tests set `DSH_HOME` so this directory is `profiles/kss`):

```sh
node ./node_modules/@deepseek-ai/dsh/lib/bin.js --profile kss --dump-config
```
