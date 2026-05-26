"""Forme Studio modules.

Each module is a self-contained vertical (routes + schemas + presets +
module-specific services) that plugs into the FastAPI app via its `router`
attribute. Module #1 is `packaging`; future modules might be `apparel`,
`signage`, etc.
"""
