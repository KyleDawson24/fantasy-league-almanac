# Fantasy League Almanac — Homepage Copy (DRAFT)

Fantasy League Almanac is an open-source desktop tool that turns your ESPN
fantasy-baseball league history into a readable almanac. By default, source
data stays on your computer in Parquet and DuckDB. If you choose the Google
workbook flow, the app asks only for `drive.file`, creates a new spreadsheet in
your own Drive, writes the almanac, and asks before setting that workbook to
“Anyone with the link — Viewer.” It cannot use that permission to browse or
read other files in your Drive.

The v1.9 public Google workflow supports Windows because it stores the Google
grant in Windows Credential Locker and refuses plaintext fallback. Fantasy
League Almanac has no hosted user accounts, analytics, advertising, or
maintainer backend, and it does not automatically send Kyle your cookies,
tokens, league data, or workbook link.

- Download/source: [GitHub repository — INSERT PUBLIC URL]
- Privacy Policy: `/privacy/`
- Terms of Service: `/terms/`
- Support and privacy inquiries: kpdawson.github@gmail.com

Operator: Kyle Dawson, an individual. Fantasy League Almanac is not affiliated
with or endorsed by ESPN, Google, Snowflake, or Major League Baseball.

Publication target: `https://[KYLE-CONTROLLED-VERIFIED-DOMAIN]/`. The shared
`github.io` domain is not represented as Kyle-owned for Google domain
verification. A custom domain Kyle controls may point to GitHub Pages.
