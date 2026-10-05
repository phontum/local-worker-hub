"""Structured providers for deterministic public data (weather, exchange rates, clocks).

The ask pipeline lets the decide call pick one of these instead of a generic web search. Providers fetch from a
documented keyless API through the DNS-pinned public transport and return host-written text, so the answer model quotes
exact values. They never receive repository, account or private context: only a place name, currency codes or time zones.
"""
