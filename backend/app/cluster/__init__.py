"""Cluster handoff — CRD-primary GShareSession apply + signed status callback receipt.

This plane does NOT call workload K8s APIs (Pod/Service/Ingress/Secret). It only applies the
GShareSession CR and receives status callbacks.
"""
