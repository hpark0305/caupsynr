# Vendored front-end libraries

Served from this origin so authenticated portal pages do not execute code from
third-party CDNs. Files are unmodified copies of the npm package builds.

| File | Package | Source file | SHA-256 |
|---|---|---|---|
| chart-4.4.1.umd.min.js | chart.js@4.4.1 | dist/chart.umd.js | 74401d738dd3e03ee5dfb3b6841210fe2c4ead8a960c4011ca4ba0b78a9fd8f3 |
| gsap-3.12.5.min.js | gsap@3.12.5 | dist/gsap.min.js | 28033e449a31ebcc396e5be8b13b63152bf03094288fb5867034321927bce087 |

To update: `npm pack <package>@<version>`, copy the listed file, update this
table, and change the template `<script src>` paths.
