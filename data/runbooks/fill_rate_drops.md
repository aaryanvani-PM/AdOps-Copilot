# Fill Rate Drops

## Definition
Fill rate = ad requests that returned an ad / total ad requests. Healthy fill on live-stream placements is 85% or higher.

## Severity
- Minor: fill between 70% and 85% for one day.
- Major: fill below 70% for two or more consecutive days. Treat as an incident.

## Likely causes
1. Demand partner outage or a demand partner pausing bids.
2. Floor price set above what buyers are bidding.
3. SSAI stitching errors on live streams, so ad pods are not filled in time.
4. Creative or format mismatch with the placement.

## Diagnosis steps
1. Find the first day fill dropped and check whether it affects one campaign or a whole placement.
2. If the whole placement dropped, check demand partner status and floor prices.
3. If only one campaign dropped, check its creative and targeting.

## Escalation
Major fill drops on live placements go to the ad-ops on-call within 2 hours (see the escalation policy).
