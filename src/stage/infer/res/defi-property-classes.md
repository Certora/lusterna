---
name: defi-property-classes
description: The classes of properties to look for in a program, each as a question to ask of a component. Use before you draft the properties of a component.
---

# Property classes

Work through this list for every component, before you draft. For each class, note the property that it produced, or one line on why the component does not expose that surface. A class that you skip leaves a part of the program with no properties.

Each class is a question. Ask it of the component and answer it from the intent of the program.

State an attack as the claim that the attack cannot happen. Do not state a property about collisions of hashes or signatures, or about log messages.

## The classes

**Value conservation.** Can the total of everything the program owes exceed what it holds? Who can make that happen? This applies wherever claims are recorded against a pool. Look for claims that are recorded outside the main total.

**Who absorbs a change in value over time.** A flow records an amount or a rate at one moment and acts on it later. In between, the value behind it moves. Someone absorbs that movement. This applies to every flow with two steps. State who is meant to absorb it. Do not state that the amount of the second step equals the amount recorded in the first step: that restates the implementation, and if the implementation is wrong, the property holds for the bug.

**What going first is worth.** An actor sees a call before it takes effect. What is it worth to act directly before or after it? This applies wherever state that everyone can read feeds a price, a rate, a share count or a fee, and some function updates that state. State it as: no sequence of calls gives the actor a profit. A call that commits value at a rate the caller cannot bound is exposed, and the missing bound is a finding.

**Which way each division rounds, and who gains.** Every division favors one side. This applies to every conversion between units, every truncation to a coarser precision, and every pair of functions that convert in opposite directions. State it as: a round trip never returns more than it took, and a truncated remainder is refunded or accounted for.

**The empty state and the first participant.** The component holds nothing, or one unit. What happens then? Can someone change its holdings directly, without the accounting that tracks them? This applies wherever a price or a share is a ratio of two quantities and either quantity can change from outside.

**What must not be blocked, and who can block it.** Which operations must succeed, for whom, and who can stop them? A program can satisfy every property of the form "if the call succeeds, its effects are correct" and still be unusable. This applies to a loop over participants, to a call into code that a participant chose, to an operation behind a threshold or a position in a queue, and to a dependency on an external actor that fails. State it as a call that does not fail under the stated conditions, with the untrusted callee free to fail. A minimum amount that is meant as a guard can leave a small position with no way out.

**Who the attacker is allowed to be.** For each privileged action, who performs it? One half is that a caller without the privilege cannot reach the privileged effect. The other half applies to every property about an attacker: the attacker does not hold the privileges that the program gives to trusted parties. A trusted administrator who attacks the program is not a finding. A change of privilege belongs here, including a handover that leaves a required role with no holder.

**How stale a value from elsewhere is allowed to be.** The component reads a value that something else updates, and acts on it. How old may the value be at that moment, and what does its age cost? This applies to every read whose source changes independently of the call that reads it.

**What a callee can do with what it is given.** The component calls code that someone else controls and passes it state that it reads again afterwards. At the moment of the call, which of its own invariants do not hold, and which parts of the passed state can the callee change? Ask about the state at that moment, whatever the shape of the code.

**Fees, splits and intermediaries.** Does the sum of what every party receives equal what was paid? Can a share be taken twice, be avoided through another path, or be applied to a quantity that it was not meant for?

**Lifecycle.** Which states can the component be in, which transitions exist, and which operations stay available in each state? The properties to look for connect these: an operation that is still available in a paused state, a transition that leaves value with no owner, a state that nothing leaves.

**Two roles, one value.** Two arguments of an operation are the same value, or two parties are the same party. What happens then, and at the extremes of an amount? If this breaks a step of the accounting, it is one property.

## Kinds

A class that is about one struct between operations gives an invariant. Every other class gives a rule. A rule can state that something must be possible: "a holder with a sufficient balance can always exit" and "no participant can make the withdrawal of another participant fail" are rules.
