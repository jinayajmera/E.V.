/* ==========================================================================
   E-VOTING SYSTEM - FINAL : NEGATIVE TESTS  (every statement below MUST FAIL)
   Proves that constraints, triggers and procedures protect the data.
   They fail, so nothing is changed.  Run one statement at a time in Workbench
   (the red "Error Code" line in the Output panel is the expected result), or let
   `python tests/run_negative_sql.py` check all of them automatically.
   Expected state: sample data as in the sample data, election 3 (GEN2026) Active.
   ========================================================================== */
USE evoting;

-- EXPECT: not open for voting
-- N1. Vote in an election that is already Closed (procedure sp_cast_vote)
CALL sp_cast_vote(1, 1, 1, @v);

-- EXPECT: already voted
-- N2. Same voter votes twice in election 3
CALL sp_cast_vote(1, 3, 1, @v);

-- EXPECT: Duplicate entry
-- N3. Same, but by inserting straight into Vote - UNIQUE(VoterID, ElectionID) stops it
INSERT INTO Vote (VoteID, VoterID, ElectionID, CandidateID, VoteTime) VALUES (9101, 1, 3, 1, NOW());

-- EXPECT: not active
-- N4. Voter 8 (Neha) has an Inactive account - she cannot vote in election 3
CALL sp_cast_vote(8, 3, 4, @v);

-- EXPECT: does not exist
-- N5. Voter 99 does not exist
CALL sp_cast_vote(99, 3, 1, @v);

-- EXPECT: not approved in this voter
-- N6. Voter 6 lives in Chennai but chooses candidate 1 who contests in Vellore (trigger trg_vote_business_rules)
CALL sp_cast_vote(6, 3, 1, @v);

-- EXPECT: not open for voting
-- N7. Direct INSERT into Vote for a Closed election is blocked by trigger trg_vote_election_open
INSERT INTO Vote (VoteID, VoterID, ElectionID, CandidateID, VoteTime) VALUES (9102, 6, 1, 3, NOW());

-- EXPECT: cannot be re-opened
-- N8. Trigger trg_election_status_guard : a Closed election is final
UPDATE Election SET Status = 'Active' WHERE ElectionID = 1;

-- EXPECT: Nominations are closed
-- N9. Trigger trg_nomination_rules : no nominations for a Closed election
INSERT INTO CandidateNomination (NominationID, CandidateID, ElectionID, ConstituencyID, NominationDate, Status)
VALUES (9201, 3, 1, 2, CURDATE(), 'Pending');

-- EXPECT: Only an Active election
-- N10. Closing an election that is not Active
CALL sp_close_election_and_generate_results(1, @r);

-- EXPECT: closed election
-- N11. Adding voters to a Closed election
CALL sp_assign_constituency_voters(1, 1, @n);

-- EXPECT: foreign key constraint
-- N12. Referential integrity (ON DELETE RESTRICT) : constituency 1 still has voters
DELETE FROM Constituency WHERE ConstituencyID = 1;

-- EXPECT: chk_election_dates
-- N13. CHECK constraint : election cannot end before it starts
UPDATE Election SET EndDate = '2000-01-01' WHERE ElectionID = 3;

-- EXPECT: chk_voter_age
-- N14. CHECK constraint : voter must be 18+ on the registration date
INSERT INTO Voter (VoterID, Username, Password, Name, DOB, Gender, Email, RegistrationDate, ConstituencyID)
VALUES (9301, 'kid', 'x', 'Too Young', '2015-01-01', 'Male', 'kid@example.com', CURDATE(), 1);

-- EXPECT: Duplicate entry
-- N15. A second voter account with the SAME identity number (hash) is refused - one person, one account
INSERT INTO Voter (VoterID, Username, Password, AccountStatus, Name, DOB, Gender, Email, Phone, RegistrationDate, ConstituencyID, NationalIDHash, NationalIDLast4)
SELECT 9201, 'clone_one', 'x', 'Active', 'Clone One', '1990-01-01', 'Male', 'clone.one@example.com', '9000000001', CURDATE(), ConstituencyID, NationalIDHash, NationalIDLast4 FROM Voter WHERE VoterID = 1;

-- EXPECT: Duplicate entry
-- N16. The same phone number cannot be used by two voters (last 10 digits compared)
INSERT INTO Voter (VoterID, Username, Password, AccountStatus, Name, DOB, Gender, Email, Phone, RegistrationDate, ConstituencyID)
SELECT 9203, 'clone_three', 'x', 'Active', 'Clone Three', '1990-01-01', 'Male', 'clone.three@example.com', CONCAT('+91 ', RIGHT(Phone, 10)), CURDATE(), ConstituencyID FROM Voter WHERE VoterID = 1;

-- EXPECT: Duplicate entry
-- N17. The same photograph (SHA-256) cannot belong to two voters
UPDATE Voter SET PhotoSha256 = (SELECT s FROM (SELECT PhotoSha256 AS s FROM Voter WHERE VoterID = 1) t) WHERE VoterID = 2;

-- EXPECT: cannot be changed
-- N18. The identity number of a registered voter is immutable
UPDATE Voter SET NationalIDHash = REPEAT('a', 64) WHERE VoterID = 1;
