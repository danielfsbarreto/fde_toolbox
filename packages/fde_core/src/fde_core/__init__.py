"""The shared contract of the FDE Activity Log: daily log models (domain) and MongoDB access (repositories).

Final daily logs are immutable and are the record of what happened; every flow reads them through this package.
No CrewAI here: flows add the agents.
"""
