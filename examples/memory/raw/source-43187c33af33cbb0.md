# Storage decision

Fictional engineering note by Alex.

Project Orbit will use SQLite for application metadata because the pilot runs on
one server and has few writers. Markdown files remain the authoritative store
for handbook content. The team will reconsider PostgreSQL if concurrent writes
become a measured problem. Alex owns the storage decision.
