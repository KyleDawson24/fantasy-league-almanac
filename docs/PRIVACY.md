# Privacy

League-member names in this repository's seeds are synthetic stand-ins for the
real league's members; the real data stays on the maintainer's machine and a
private remote. **No contact information -- email, phone, address -- is ever
committed, in any form, in any file.**

Platform member GUIDs *are* retained. They are the identifiers the
identity-resolution layer joins on, and removing them would break the thing
this project is about. So the committed seeds are name-anonymized, not
identifier-anonymized, and this file says so rather than claiming more.

The regression corpora that render real names are private by design, and the
tests that need them skip in a public clone rather than fail.

If you are a league member and want your stand-in changed, email the address in
the [README](../README.md#contact).
