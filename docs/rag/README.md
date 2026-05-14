# RAG Seed Documents

This directory contains the initial high-value knowledge base for Blender 4.5.4
LTS code generation.

- `blender_4_5_official_sources.md`: official Blender source index, priorities,
  and retrieval triggers.
- `blender_4_5_high_value_notes.md`: concise coding rules and failure patterns
  for agents.

The intended retrieval order is:

1. Project cookbook and high-value notes.
2. Version-locked official source pages.
3. Full official API page chunks only when a specific symbol is needed.

Use Blender 4.5 documentation as the API baseline for Blender 4.5.4 LTS.
