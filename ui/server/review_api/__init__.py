"""Thin backend of the FDE Activity Log review app: direct item edits on the stored day, the AMP Human in the Loop
webhook, and answering the flow's review pause. Independent of the AMP flow (flows/daily_log): it shares
only the MongoDB documents, as described in contract.py.
"""
