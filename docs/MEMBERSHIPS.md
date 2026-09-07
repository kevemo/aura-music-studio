# Elevate Souls Productions Content Creation Command Center — Membership System

**Powered by Aura AI**  
**Elevate Your Soul Through Purposeful Media**

All accounts require approval by Elevate Souls Productions. New membership requests are routed to the configured ESP approval inbox for Kev or Mary to approve or reject.

The public membership names are Free, Member and Unlimited Pro. For backwards compatibility with persisted accounts and integrations, the internal plan IDs remain `free`, `base` and `pro`.

## Free — £0/month

Designed to let a new member experience Aura and the Command Center before paying.

Included:
- Account and project creation
- Basic song/concept creation
- Basic AI lyric assistance
- Aura Producer basic planning/chat
- Basic preview workflow
- Access to the public studio/project interface

Not included:
- Confirmed finished full-track allowance
- Finished master downloads
- WAV/FLAC downloads
- Backing-track production
- Stem splitter
- Stem downloads
- Advanced mastering
- Multitrack DAW
- Sample Lab
- Style DNA
- Approved voice duplication
- Audio-to-MIDI control transcription
- Advanced repaint/remix/cover tools
- AuraSec subscription entitlement

## Member — £4.99/month

For users who want to create finished content regularly without needing the complete professional toolset.

Included:
- Everything in Free
- **1 confirmed finished full track per day** in the existing music entitlement model
- **Unlimited regenerations of that day's track until the user confirms the desired result**
- Confirmation is the event that consumes the daily finished-track allowance
- MP3 final master download
- WAV final master download
- Basic mastering
- Authorized audio uploads
- Score/MIDI/MusicXML uploads as control data
- Backing-track creation
- Harmony Architect/basic backing-harmony workflow

Member does not include the Unlimited Pro-only production suite such as full stem splitting, multitrack DAW, Sample Lab, Style DNA, advanced voice duplication, unlimited finished tracks or the AuraSec subscription entitlement.

## Unlimited Pro — £9.99/month

The highest enabled Command Center membership tier.

Included:
- Everything in Free and Member
- **Unlimited confirmed full tracks**
- **Unlimited regeneration**
- MP3 / WAV / FLAC downloads
- Stem splitter / source separation
- Individual stem downloads
- BandLab/multitrack export
- Full multitrack DAW
- Take lanes
- Automation
- Advanced and reference mastering
- Covers/remixes of authorized material
- Region repaint/replace/extend
- Sample Lab
- Multi-reference Style DNA
- Harmony Architect
- Consent-gated approved voice duplication/conversion
- Audio-to-MIDI control transcription
- Full Producer tools
- Priority generation queue when queue prioritization is enabled
- Every enabled Unlimited Pro Command Center production feature
- **AuraSec entitlement included with the subscription**

AuraSec may also be distributed and sold separately under its own approved commercial catalogue. This document does not invent or hardcode a standalone AuraSec price.

## Member daily-track rule

A Member-tier user starts a daily song slot when they generate a full track. Aura may regenerate that same unconfirmed project repeatedly without consuming another daily track. When the member presses **Confirm Track**, that song is marked confirmed and consumes the day's one-track allowance. They can continue using non-full-track tools, but another finished Member-tier track cannot be confirmed until the next daily allowance period.

The current implementation uses the existing deterministic daily-boundary logic. Any future profile-timezone reset must preserve the same server-authoritative entitlement architecture.

## Approval and payment lifecycle

1. User selects Free, Member or Unlimited Pro and signs up.
2. The persisted requested plan ID remains `free`, `base` or `pro`.
3. Account status becomes `pending_approval`.
4. An approval request is sent through the configured ESP approval workflow.
5. Kev or Mary approves or rejects the request through the configured single-use review flow.
6. Free becomes active immediately after approval.
7. Approved Member/Unlimited Pro accounts become `approved_pending_payment`.
8. The member receives the configured ESP payment instructions.
9. Paid access activates only after payment is verified/admin-confirmed by the authoritative billing path.
10. The membership engine exposes only the features included in the member's active plan.

## Current PayPal payment links

The repository currently retains configured manual invoice/payment links for the paid membership plan IDs. Those provider links are deployment configuration and must charge the same authoritative GBP amounts as the plan catalogue: Member £4.99 and Unlimited Pro £9.99.

A browser redirect alone is not payment proof. Paid access activates only through the verified billing/admin-confirmation path.

## Ownership position

The Elevate Souls Productions Content Creation Command Center and Elevate Souls Productions do not claim ownership of a member's original inputs or eligible generated outputs. Rights in AI-assisted outputs remain subject to applicable law, licences of underlying open models, and any third-party/source-material rights. Members must have the rights required for material they upload or ask the Command Center to transform.
