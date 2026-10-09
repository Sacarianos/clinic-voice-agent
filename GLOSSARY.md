# Clinic Voice Agent

A phone receptionist for a fictional single-location family medicine clinic. It books, reschedules and cancels appointments for patients who prove who they are on the call.

## People

**Patient**:
An adult with a record in the clinic's EHR.
_Avoid_: user, customer, member

**Caller**:
Whoever is on the phone. A caller is not treated as a patient until Identity Verification succeeds.
_Avoid_: user

**Proxy Caller**:
A caller acting for someone else, such as a parent or caregiver. Proxy callers always get a Handoff.
_Avoid_: guardian, representative

**Provider**:
A clinician patients book with. The clinic has two physicians and one nurse practitioner.
_Avoid_: doctor, practitioner

## Identity

**Identity Verification**:
Matching the caller's stated name and date of birth to exactly one patient. The date of birth must match exactly. The surname may match by sound, or with one wrong letter per five as long as the first letter is right, so ordinary speech recognition slips still match. When more than one patient matches, the caller is asked to spell the surname, and only that spelled attempt may pick the one whose surname it spells exactly. If it still matches more than one, the call ends in a Handoff.
_Avoid_: authentication, login

**Verified Patient**:
A caller who passed Identity Verification on the current call. Verification never carries over between calls.

## Scheduling

**Slot**:
A 30-minute block of one provider's time, either free or busy.
_Avoid_: opening, availability

**Appointment**:
One patient's visit with one provider in one slot.
_Avoid_: booking, visit, reservation

**Visit Type**:
The reason label on an appointment: annual physical, sick visit or follow-up. It does not change the appointment's length.

**Booking Window**:
The span of dates a caller can book into: from now until two weeks out. Past appointments can't be changed.

**Book**:
Create a new appointment in a free slot.

**Reschedule**:
Move an existing appointment to a different slot. It stays the same appointment.
_Avoid_: rebook, move

**Cancel**:
End an appointment and free its slot.

**Read-back**:
The agent saying the exact provider, date, time and visit type, followed by the caller's clear yes. Every Book, Reschedule and Cancel needs one.
_Avoid_: confirmation

## Callbacks

**Handoff**:
Ending the automated conversation and creating a Callback Request so clinic staff call the caller back.
_Avoid_: transfer, escalation

**Callback Request**:
A record asking clinic staff to call a caller back, with the reason and the Callback Number.
_Avoid_: ticket, follow-up task

**Callback Number**:
The phone number a Callback Request calls back: the call's caller ID, or, on a call without one such as a browser call, a number the caller gives. It never verifies anyone.
_Avoid_: caller phone, contact number

**Emergency Redirect**:
Telling the caller to hang up and dial 911, then ending the call and filing a Callback Request marked as an emergency. The caller never waits on the line for staff. On a call without caller ID, the agent asks for a Callback Number only after the 911 line, and only if the caller can give one.

**Clinic Question**:
A question about the clinic itself: hours, address, parking, or which provider does what. Clinical questions are not Clinic Questions and always get a Handoff.
_Avoid_: FAQ, general question
