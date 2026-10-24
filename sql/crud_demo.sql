/* ==========================================================================
   E-VOTING SYSTEM : CRUD DEMONSTRATION IN PURE SQL  (evoting)
   Shows Create / Read / Update / Delete AND the stored procedures, functions,
   views and triggers working together on one complete mini-election.
   * Uses IDs 9001+ so it never touches your real rows.
   * Cleans up after itself (section 9), and also cleans up at the start, so you
     can run the whole file again and again.
   * Run it step by step in MySQL Workbench (select a block, Ctrl+Enter) while
     explaining, or run it all at once.
   ========================================================================== */
USE evoting;

-- 0. Clean-up of any earlier demo run (children first - referential integrity!) ------
DELETE FROM ElectionResult      WHERE ElectionID = 9001;
DELETE FROM Vote                WHERE ElectionID = 9001;
DELETE FROM ElectionVoter       WHERE ElectionID = 9001;
DELETE FROM CandidateNomination WHERE ElectionID = 9001;
DELETE FROM Election            WHERE ElectionID = 9001;
DELETE FROM ElectionVoter       WHERE VoterID    = 9001;
DELETE FROM Voter               WHERE VoterID    = 9001;

-- 1. CREATE : a new voter (Vellore Central) -----------------------------------------
INSERT INTO Voter (VoterID, Username, Password, AccountStatus, Name, DOB, Gender, Address, Email, Phone, RegistrationDate, ConstituencyID,
                   NationalIDHash, NationalIDLast4, VerificationStatus)
VALUES (9001, 'demo_voter', SHA2('demo1234', 256), 'Active', 'Demo Voter', '1999-03-03', 'Female',
        '1 Demo Street, Vellore', 'demo.voter@example.com', '9555555555', CURDATE(), 1,
        SHA2('demo-identity-9001', 256), '9001', 'Verified');      -- identity stored only as a hash; Verified so the voter may vote

-- 2. READ : the new row, and its age through a function in the SELECT -------------------
SELECT VoterID, Name, Email, ConstituencyID, TIMESTAMPDIFF(YEAR, DOB, CURDATE()) AS Age FROM Voter WHERE VoterID = 9001;

-- 3. UPDATE : change the phone number and address -------------------------------------
UPDATE Voter SET Phone = '9666666666', Address = '2 New Demo Street, Vellore' WHERE VoterID = 9001;
SELECT VoterID, Phone, Address FROM Voter WHERE VoterID = 9001;

-- 4. CREATE : a new election with two nominations -------------------------------------
INSERT INTO Election (ElectionID, ElectionCode, ElectionName, Description, ElectionType, StartDate, EndDate, Status, OfficerID)
VALUES (9001, 'DEMO2026', 'Demo Local Election', 'Created by crud_demo.sql', 'Local', CURDATE(), DATE_ADD(CURDATE(), INTERVAL 1 DAY), 'Upcoming', 1);

INSERT INTO CandidateNomination (NominationID, CandidateID, ElectionID, ConstituencyID, NominationDate, Status)
VALUES (9001, 1, 9001, 1, CURDATE(), 'Pending'),
       (9002, 2, 9001, 1, CURDATE(), 'Pending');

-- 5. UPDATE : the officer approves both nominations (trigger trg_nomination_audit logs it)
UPDATE CandidateNomination SET Status = 'Approved' WHERE ElectionID = 9001;
SELECT * FROM v_ballot WHERE ElectionID = 9001;                       -- VIEW : the ballot

-- 6. PROCEDURE : assign every Active voter of Vellore Central to the election -----------
CALL sp_assign_constituency_voters(9001, 1, @added);
SELECT @added AS VotersAdded;
SELECT VoterID, EligibilityStatus, HasVoted FROM ElectionVoter WHERE ElectionID = 9001;

-- 7. Open the election, vote, look at the results ------------------------------------------
UPDATE Election SET Status = 'Active' WHERE ElectionID = 9001;
SELECT fn_can_vote(9001, 9001) AS CanVoteBefore;                      -- FUNCTION -> 1
CALL sp_cast_vote(9001, 9001, 1, @voteid);                            -- PROCEDURE (transaction)
SELECT @voteid AS NewVoteID;
SELECT fn_can_vote(9001, 9001) AS CanVoteAfter;                       -- FUNCTION -> 0
SELECT HasVoted, VotedTimestamp FROM ElectionVoter WHERE ElectionID = 9001 AND VoterID = 9001;   -- set by trigger
SELECT ElectionCode, EligibleVoters, VotesCast, TurnoutPercent FROM v_election_summary WHERE ElectionID = 9001;

CALL sp_close_election_and_generate_results(9001, @rows);            -- PROCEDURE : close + RANK() results
SELECT @rows AS ResultRowsCreated;
SELECT CandidateName, PartyName, VoteCount, ResultStatus FROM v_public_results WHERE ElectionID = 9001;

-- 8. What the triggers wrote to the audit log ------------------------------------------------
SELECT EventTime, TableName, Action, Details FROM AuditLog ORDER BY LogID DESC LIMIT 8;

-- 9. DELETE : remove everything this demo created (children first) -------------------------
DELETE FROM ElectionResult      WHERE ElectionID = 9001;
DELETE FROM Vote                WHERE ElectionID = 9001;
DELETE FROM ElectionVoter       WHERE ElectionID = 9001;
DELETE FROM CandidateNomination WHERE ElectionID = 9001;
DELETE FROM Election            WHERE ElectionID = 9001;
DELETE FROM ElectionVoter       WHERE VoterID    = 9001;
DELETE FROM Voter               WHERE VoterID    = 9001;
SELECT COUNT(*) AS DemoRowsLeft FROM Voter WHERE VoterID = 9001;      -- 0
