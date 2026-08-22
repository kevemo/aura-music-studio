# The Live Sound Studio — Membership System

**Elevate Souls Productions Presents: The Live Sound Studio**  
**Music Making for Professionals**

All accounts require approval by Elevate Souls Productions. New membership requests are routed to `elevatesoulsproductions@gmail.com` for Kev or Mary to approve or reject.

## Free — $0/month

Designed to let a new member experience Aura and understand the studio before paying.

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

## Base — $4.99/month

For users who want to make finished music regularly without needing the complete professional toolset.

Included:
- Everything in Free
- **1 confirmed finished full track per day**
- **Unlimited regenerations of that day's track until the user confirms the desired result**
- Confirmation is the event that consumes the daily finished-track allowance
- MP3 final master download
- WAV final master download
- Basic mastering
- Authorized audio uploads
- Score/MIDI/MusicXML uploads as control data
- Backing-track creation
- Harmony Architect/basic backing-harmony workflow

Base does not include the Pro-only production suite such as stem splitting, multitrack DAW, Sample Lab, Style DNA, advanced voice duplication or unlimited finished tracks.

## Pro — $9.99/month

The complete Live Sound Studio.

Included:
- Everything in Free and Base
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
- Every enabled Live Sound Studio production feature

## Base daily-track rule

A Base member starts a daily song slot when they generate a full track. Aura may regenerate that same unconfirmed project repeatedly without consuming another daily track. When the member presses **Confirm Track**, that song is marked confirmed and consumes the day's one-track allowance. They can continue using non-full-track tools, but another finished Base track cannot be confirmed until the next daily allowance period.

The initial implementation uses a UTC daily boundary so the rule is deterministic across deployments. A later profile-timezone field can make the reset local to each member without changing the entitlement architecture.

## Approval and payment lifecycle

1. User selects Free, Base or Pro and signs up.
2. Account status becomes `pending_approval`.
3. An approval request is sent to `elevatesoulsproductions@gmail.com`.
4. Kev or Mary approves or rejects the request through a single-use review link.
5. Free becomes active immediately after approval.
6. Approved Base/Pro accounts become `approved_pending_payment`.
7. The member receives the configured ESP PayPal payment link.
8. Paid access activates only after payment is verified/admin-confirmed.
9. The membership engine then exposes only the features included in the member's plan.

## Current PayPal payment links

- Base $4.99: `https://www.paypal.com/invoice/p/#8MW58LYURC584SWJ`
- Pro $9.99: `https://www.paypal.com/invoice/p/#678LURGCLH77JDGH`

These are currently configured as manual invoice/payment links, not automatic recurring-subscription proof. The app therefore does not activate paid access from a browser redirect alone.

## Ownership position

The Live Sound Studio and Elevate Souls Productions do not claim ownership of a member's original inputs or eligible generated outputs. Rights in AI-assisted outputs remain subject to applicable law, licences of underlying open models, and any third-party/source-material rights. Members must have the rights required for material they upload or ask the Studio to transform.
