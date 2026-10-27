Preliminary patent evaluation of Room OS
Overall assessment
Present patent-readiness score: 5.5/10
Potential score after focused technical innovation and validation: 8/10
The project is useful, commercially relevant and technically feasible. However, the present description mainly combines several already-known elements:
- Wi-Fi CSI-based human sensing
- ESP32 transmitter–receiver arrangement
- Activity classification
- Room calibration
- Fall or anomaly detection
- Temporal activity tracking
- Local dashboard and event history
A patent application can certainly be prepared, but the present broad concept may face novelty and inventive-step objections unless the application identifies a precise technical mechanism that differs from existing CSI-sensing systems.
Indian patentability requires novelty, inventive step and industrial applicability. An inventive step should produce a technical advance or economic significance and should not be obvious to a skilled person. Government of India
## 1. Component-Wise Evaluation
| Criterion | Score | Evaluation |
|---|---:|---|
| Problem relevance | 9/10 | Strong need for privacy-preserving indoor intelligence without cameras, microphones or wearables |
| Technical feasibility | 8/10 | Two ESP32 boards and a laptop can form a practical prototype |
| Industrial applicability | 8/10 | Applicable to elderly care, smart rooms, hostels, hospitals, offices and security |
| Novelty of CSI sensing | 3/10 | CSI-based presence and activity sensing is well established |
| Novelty of ESP32 implementation | 3/10 | ESP32-based CSI activity recognition has already been published |
| Novelty of fall detection | 3/10 | Several CSI-based fall-detection systems and patents already exist |
| Novelty of temporal interpretation | 5/10 | Potentially useful, but sequence models and temporal activity reasoning already exist |
| Novelty of explainability | 6/10 | Promising if explanation is generated directly from physical CSI evidence rather than generic AI confidence |
| Technical specificity | 4/10 | The current document describes functions but not a distinctive algorithm, equation or decision architecture |
| Patent-drafting readiness | 5/10 | The system architecture exists, but claimable technical steps and comparative results are still required |

2. Main prior-art concerns
ESP32-based activity recognition
An IEEE publication already describes a low-cost, non-intrusive human-activity recognition system using CSI collected through ESP32 and classifies walking, running and standing using CNN, LSTM and hybrid models. Therefore, claims based only on “ESP32 plus CSI plus activity recognition” are unlikely to be sufficiently novel. doi.org
Indoor activity recognition
Existing patents already cover collecting CSI between a Wi-Fi transmitter and receiver, removing noise, selecting relevant subcarriers and classifying indoor activities such as sitting, standing and walking. Google Patents
Occupancy and room-state detection
Patents already disclose CSI-based passive occupancy detection for states including moving, standing, sitting and sleeping. Some existing systems also use room-state machines to distinguish occupied, unoccupied and moving conditions. Google Patents
Fall detection
CSI-based fall detection is mature prior art. One patent uses interference assessment, dynamic subcarrier selection, multi-link fusion and an SVM classifier to recognize falls. Earlier research also combines CSI amplitude and phase to separate falls from similar activities. Google Patents
Adaptive calibration and anomaly prediction
Automatic CSI calibration, cross-environment adaptation, temporal prediction and abnormal-behaviour detection have also been disclosed. Google Patents
Consequently, the patent should not claim the entire idea simply as:
“A system using two ESP32 devices and Wi-Fi CSI to recognize activities and detect falls.”

That claim would probably be too broad and vulnerable to prior art.
Recommended central invention
The project should be redesigned around one distinctive technical contribution:
Self-Calibrating Causal Room-State Engine Using Wi-Fi CSI
Instead of claiming ordinary activity classification, claim a system that:
1. Establishes a multidimensional empty-room channel baseline.
2. detects environmental drift separately from human-induced channel variation;
3. constructs a continuously updated room-state vector;
4. interprets a sequence of state transitions rather than a single activity label;
5. generates anomaly evidence using physically measurable CSI events;
6. validates an anomaly through a confirmation-transmission stage; and
7. updates only safe baseline components without learning an abnormal event as normal.
This can provide a clearer technical effect: improved cross-room robustness, fewer false alarms, reduced recalibration effort and explainable event detection using low-cost hardware.
Innovations that should be added
1. Dual-timescale adaptive baseline
Maintain two CSI baselines:
- Short-term baseline: captures immediate channel conditions.
- Long-term baseline: captures gradual changes caused by furniture movement, temperature, humidity or device displacement.
Define a drift measure such as:

```text

D_t=\alpha D_{\text{amplitude}}+\beta D_{\text{phase}}
+\gamma D_{\text{subcarrier}}+\delta D_{\text{packet}}

```
The system should distinguish:
- gradual environmental drift;
- temporary wireless interference;
- human movement; and
- permanent room-layout change.
Patent value: This is stronger than ordinary one-time room calibration if the update logic is technically specified and experimentally validated.
2. Protected baseline update mechanism
A serious problem in adaptive systems is that an unusual event may accidentally be learned as the new normal.
Introduce a baseline protection gate:

```text

B_{t+1}=
\begin{cases}
(1-\eta)B_t+\eta X_t, & \text{if stable and anomaly-free}\\
B_t, & \text{if activity or anomaly is detected}
\end{cases}

```
The baseline should be updated only when:
- room stability exceeds a specified threshold;
- anomaly probability is below a threshold;
- packet quality is acceptable;
- no significant human activity is present; and
- the state remains stable for a specified validation period.
Patent value: This can become a defensible claim because it solves a technical failure of adaptive CSI sensing.
3. Causal anomaly signature instead of simple fall classification
Do not classify a fall using only one CSI window. Detect a causal sequence:

```text

\text{Rapid energy rise}
\rightarrow
\text{vertical-motion signature}
\rightarrow
\text{abrupt energy decay}
\rightarrow
\text{prolonged low-motion state}

```
A possible anomaly score is:

```text

A_t=w_1E_{\text{spike}}+
w_2R_{\text{decay}}+
w_3T_{\text{inactivity}}+
w_4C_{\text{transition}}-
w_5Q_{\text{interference}}

```
where:
- \(E_{\text{spike}}\) represents sudden CSI energy change;
- \(R_{\text{decay}}\) represents post-event reduction;
- \(T_{\text{inactivity}}\) represents inactivity duration;
- \(C_{\text{transition}}\) represents consistency with the expected event sequence; and
- \(Q_{\text{interference}}\) penalizes unreliable measurements.
Patent value: The novelty would lie in the particular sequence, feature calculation, confidence fusion and confirmation process—not merely in detecting a fall.
4. Active verification using controlled probe packets
After detecting a possible anomaly, the transmitter should temporarily change its sensing pattern:
- increase packet rate;
- switch between selected Wi-Fi channels;
- vary transmission power within permitted limits;
- send a predetermined probing sequence; or
- alternate packet intervals.
The receiver then examines whether the suspected room condition produces a consistent response across the verification probes.
This creates a two-stage operation:

```text

\text{Passive monitoring}
\rightarrow
\text{Suspicion}
\rightarrow
\text{Active verification}
\rightarrow
\text{Confirmed or rejected event}

```
Patent value: This is one of the strongest additions because the system actively changes the communication link to verify an inferred physical event. It provides a measurable technical interaction between hardware and environment.
5. Reliability-aware subcarrier selection
Instead of feeding all CSI subcarriers to the model, calculate a reliability score:

```text

R_k=
\lambda_1S_k+
\lambda_2V_k+
\lambda_3C_k-
\lambda_4N_k-
\lambda_5I_k

```
where:
- \(S_k\): activity sensitivity;
- \(V_k\): temporal stability;
- \(C_k\): cross-window consistency;
- \(N_k\): noise contribution;
- \(I_k\): interference susceptibility.
Only reliable subcarriers should participate in activity and anomaly inference.
Dynamic subcarrier selection is already known, so your innovation must use a different scoring formulation, baseline relationship or task-specific selection procedure. The technical advantage must be demonstrated against fixed-subcarrier and all-subcarrier methods. Google Patents
6. Room-state vector with state-transition constraints
Represent the room using a defined state vector:

```text

\mathbf{R}_t=
[P_t,A_t,M_t,S_t,Q_t,U_t,C_t]

```
where:
- \(P_t\): presence probability;
- \(A_t\): detected activity;
- \(M_t\): movement intensity;
- \(S_t\): room stability;
- \(Q_t\): CSI signal quality;
- \(U_t\): anomaly score;
- \(C_t\): decision confidence.
Create permitted and suspicious state transitions.
For example:
- Sitting → Standing → Walking: normal
- Walking → High-energy event → No movement: suspicious
- No movement → No movement: insufficient by itself to declare a fall
- Poor signal quality → Apparent motion: unreliable and withheld
Patent value: The exact state construction and transition-control logic may be claimable if it produces demonstrably fewer false alarms.
7. Physics-grounded explanation engine
The “Why?” explanation should not merely state model confidence. It should report measurable evidence:
Possible fall detected because CSI energy increased sharply across 18 reliable subcarriers, the motion signature decayed within 0.8 seconds, and no significant movement was observed for 35 seconds. Signal reliability was 91%.

The explanation should be generated from:
- affected subcarrier group;
- amplitude change;
- calibrated phase change;
- Doppler or spectral-energy shift;
- event duration;
- post-event inactivity;
- baseline deviation; and
- signal-quality assessment.
Explainable CSI-based activity recognition has recently become an explicit research area, so a generic explanation layer alone is unlikely to be sufficiently novel. Your system should connect the explanation to the event-confirmation control procedure. arxiv.org
8. Confidence-aware abstention
The system should be allowed to report:
- Normal event
- Possible anomaly
- Confirmed anomaly
- Insufficient signal quality
- Unknown activity
It must avoid forcing every CSI window into a known activity class.
For example:

```text

C_t<C_{\min}
\Rightarrow
\text{abstain and initiate active verification}

```
This could reduce unsafe false decisions and differentiate the system from ordinary classifiers.
9. Privacy-preserving room memory
Store only derived room events rather than raw CSI indefinitely:
- timestamp;
- room-state transition;
- anomaly evidence;
- confidence;
- signal-quality indicator; and
- anonymized event identifier.
Add:
- configurable raw-CSI deletion;
- local encryption;
- integrity hash for event records;
- no cloud transmission by default; and
- role-based access to event history.
Privacy controls alone may not establish inventive step, but they strengthen commercial value and system completeness.
10. Automatic device-displacement detection
If either ESP32 is moved, the earlier room baseline may become invalid. Introduce a device-displacement detector based on:
- persistent phase-offset change;
- broad simultaneous subcarrier shift;
- altered multipath profile;
- unchanged human-motion characteristics; and
- change lasting beyond a predetermined period.
The system should then:
1. suspend activity decisions;
2. mark the baseline as invalid;
3. initiate controlled recalibration; and
4. verify the new baseline before resuming monitoring.
This is a strong practical innovation because device movement is a major cause of CSI-model failure.
Best claimable combination
The strongest patent direction would combine:
Adaptive protected baseline + causal state-transition anomaly detection + active Wi-Fi probing for confirmation + physics-grounded explanation.

These four parts should work together as one technical process. They should not appear as unrelated optional features.
A suitable working title is:
System and Method for Self-Calibrating Privacy-Preserving Room-State Sensing and Active Anomaly Verification Using Wi-Fi Channel State Information
### Possible Independent Claim Concept
A preliminary method claim could be structured around:
1. transmitting controlled wireless probe packets between a transmitter and receiver;
2. extracting amplitude and calibrated phase information from CSI subcarriers;
3. maintaining short-term and long-term room baselines;
4. separating environmental drift from human-induced channel disturbances;
5. generating a multidimensional room-state vector;
6. detecting a candidate anomaly from a constrained sequence of room-state transitions;
7. changing at least one transmission parameter to initiate active verification;
8. confirming or rejecting the anomaly using CSI obtained during active verification;
9. conditionally updating the room baseline only when a protected update criterion is satisfied; and
10. generating an explanation containing the physical CSI evidence supporting the confirmed event.
This claim direction is stronger than separately claiming the dashboard, ESP32 devices or activity classifier.
## Experiments Needed Before Filing
At minimum, conduct testing in:
- 5–10 different rooms;
- different transmitter–receiver positions;
- furnished and unfurnished conditions;
- multiple participants;
- different body sizes and walking speeds;
- doors open and closed;
- fans and other moving objects;
- Wi-Fi interference conditions;
- ESP32 displacement conditions; and
- normal fall-like activities such as sitting rapidly, dropping an object and lying down intentionally.
Report:
| Metric | Required comparison |
|---|---|
| Activity accuracy and macro F1-score | Proposed system versus standard classifier |
| Fall sensitivity | Percentage of actual falls detected |
| Fall specificity | Correct rejection of non-fall activities |
| False alarms per room-hour | Essential for practical deployment |
| Cross-room generalisation | Training room versus unseen room |
| Calibration time | Existing approach versus proposed approach |
| Recovery after room change | Time needed to restore reliable sensing |
| Detection latency | Time from event to decision |
| Active-verification improvement | False alarms before and after verification |
| Energy and packet overhead | Normal monitoring versus verification mode |
| Explanation fidelity | Whether reported evidence agrees with signal features |
| Device-displacement detection rate | Correct baseline invalidation |

The patent specification should include equations, thresholds, flowcharts, pseudocode, alternative embodiments and actual measured improvement.
Filing strategy for November 2026
Because the target filing date is close, the work should be tightly scheduled.
By 5 October 2026
- Freeze the central inventive concept.
- Avoid publicly disclosing the new technical details.
- Complete an inventor-wise contribution record.
- Begin a claim-oriented prior-art search.
By 20 October 2026
- Implement protected dual-baseline calibration.
- Implement causal anomaly scoring.
- Implement active probe verification.
- Record preliminary comparative results.
By 5 November 2026
- Complete the system block diagram and method flowchart.
- Finalize equations and pseudocode.
- Prepare experimental tables.
- Draft one system claim and one method claim with dependent claims.
By 15 November 2026
- Obtain a professional patentability search and registered patent-agent review.
- Rewrite the specification around technical effect and measurable improvement.
- Check inventorship and institutional ownership under KARE policy.
By the end of November 2026
File either:
- a complete specification, if the integrated system and experiments are sufficiently developed; or
- a carefully written provisional specification, if important implementation work remains.
If a provisional application is used in India, the complete specification must generally follow within 12 months. Normal publication occurs after 18 months, while early publication can be requested through Form 9. Filing, publication, examination and grant are separate stages; publication does not mean that the patent has been granted. ipindia.gov.in
Important Indian patent-drafting issue
Because much of Room OS involves signal-processing and AI software, the application should not be framed merely as an algorithm or dashboard. Section 3(k) excludes a mathematical method, computer programme per se or algorithm. Government of India
Frame it as an integrated technical system in which:
- ESP32 hardware produces controlled radio transmissions;
- CSI is physically measured;
- transmission parameters are modified during verification;
- signal quality and channel behaviour control the inference process;
- baseline update protection improves sensing reliability; and
- the invention produces measurable improvements in false alarms, calibration and robustness.
The current Indian CRI guidance emphasizes demonstrating a specific and credible technical effect beyond ordinary computing. Government of India
Final recommendation
Proceed with the patent project, but do not file the current broad Room OS description without further technical development.
As currently described, it is a good prototype and research project but only moderately strong as a patent. The most promising route is to make active anomaly verification and protected adaptive calibration the core invention, supported by a causal room-state engine and physics-based explanations.
With that redesign, working implementation, comparative results and professional claim drafting, the proposal could reasonably improve from 5.5/10 to approximately 8/10 in patent readiness. This remains a preliminary technical assessment, not a legal opinion or guarantee of publication or grant.
