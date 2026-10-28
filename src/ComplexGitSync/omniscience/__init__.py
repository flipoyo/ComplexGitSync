"""omniscience — the register of what every repository was seen to hold.

Ring: 0 (the package itself; its modules say their own rings)
Contract: the Omniscience records, the Merkle index over them and the commands
    that append to and read the register. gx4g owns the register (D7,
    Minimalism), so this folder is the part of ``cgitsync`` that leaves when
    ``ProviderMigration`` hands it over. Imports nothing at load time.
Imports: none
"""
