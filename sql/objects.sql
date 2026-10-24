/* ==========================================================================
   E-VOTING SYSTEM : VIEWS, FUNCTIONS, PROCEDURES AND TRIGGERS
   Database : evoting   (MySQL 8.0.31+ ; also runs on MariaDB 10.5+)

   SAFE TO RUN :  * It never DROPs a table or the database.
                  * It never deletes or changes existing rows.
                  * It only (re)creates its OWN objects (views, functions,
                    procedures, triggers, and the new AuditLog table), so it can be
                    run again and again without errors.
   HOW TO RUN  :  MySQL Workbench -> open this file -> click the lightning
                  button (execute whole script).  Or:  python scripts/install_objects.py
   ========================================================================== */

USE evoting;

/* --------------------------------------------------------------------------
   1. NEW SUPPORT TABLE  (additive - existing 10 tables are untouched)
   -------------------------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS AuditLog (
    LogID      INT AUTO_INCREMENT PRIMARY KEY,
    EventTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    TableName  VARCHAR(50)  NOT NULL,
    Action     VARCHAR(30)  NOT NULL,
    RecordID   INT          NULL,
    Details    VARCHAR(255) NULL
);

/* --------------------------------------------------------------------------
   2. STORED FUNCTIONS
   -------------------------------------------------------------------------- */
DROP FUNCTION IF EXISTS fn_turnout_percent;
DROP FUNCTION IF EXISTS fn_votes_for;
DROP FUNCTION IF EXISTS fn_can_vote;

DELIMITER $$

-- turnout % of an election = votes cast / eligible voters * 100
CREATE FUNCTION fn_turnout_percent(p_election INT)
RETURNS DECIMAL(5,2)
READS SQL DATA
BEGIN
    DECLARE v_eligible INT DEFAULT 0;
    DECLARE v_voted    INT DEFAULT 0;
    SELECT COUNT(*) INTO v_eligible FROM ElectionVoter
     WHERE ElectionID = p_election AND EligibilityStatus = 'Eligible';
    SELECT COUNT(*) INTO v_voted FROM Vote WHERE ElectionID = p_election;
    IF v_eligible = 0 THEN
        RETURN 0.00;
    END IF;
    RETURN ROUND(100 * v_voted / v_eligible, 2);
END$$

-- number of votes a candidate has received in an election
CREATE FUNCTION fn_votes_for(p_election INT, p_candidate INT)
RETURNS INT
READS SQL DATA
BEGIN
    DECLARE v_cnt INT DEFAULT 0;
    SELECT COUNT(*) INTO v_cnt FROM Vote
     WHERE ElectionID = p_election AND CandidateID = p_candidate;
    RETURN v_cnt;
END$$

-- 1 if the voter may vote right now in the election, else 0
CREATE FUNCTION fn_can_vote(p_voter INT, p_election INT)
RETURNS TINYINT
READS SQL DATA
BEGIN
    DECLARE v_ok INT DEFAULT 0;
    SELECT COUNT(*) INTO v_ok
      FROM ElectionVoter ev
      JOIN Election e ON e.ElectionID = ev.ElectionID
      JOIN Voter    v ON v.VoterID    = ev.VoterID
     WHERE ev.ElectionID = p_election AND ev.VoterID = p_voter
       AND ev.EligibilityStatus = 'Eligible' AND ev.HasVoted = 0
       AND e.Status = 'Active' AND v.AccountStatus = 'Active';
    RETURN IF(v_ok > 0, 1, 0);
END$$

DELIMITER ;

/* --------------------------------------------------------------------------
   3. VIEWS
   -------------------------------------------------------------------------- */
-- Ballot: approved candidates, per election and constituency
CREATE OR REPLACE VIEW v_ballot AS
SELECT e.ElectionID, e.ElectionCode, e.ElectionName, e.Status AS ElectionStatus,
       co.ConstituencyID, co.ConstituencyName,
       ca.CandidateID, ca.Name AS CandidateName, ca.Photo,
       p.PartyID, p.PartyName, p.Symbol
FROM CandidateNomination n
JOIN Election       e  ON e.ElectionID     = n.ElectionID
JOIN Candidate      ca ON ca.CandidateID   = n.CandidateID
JOIN PoliticalParty p  ON p.PartyID        = ca.PartyID
JOIN Constituency   co ON co.ConstituencyID = n.ConstituencyID
WHERE n.Status = 'Approved' AND ca.AccountStatus = 'Active';

-- A voter's standing in every election they are registered for
CREATE OR REPLACE VIEW v_voter_status AS
SELECT v.VoterID, v.Name AS VoterName, v.ConstituencyID,
       e.ElectionID, e.ElectionCode, e.ElectionName, e.ElectionType,
       e.StartDate, e.EndDate, e.Status AS ElectionStatus,
       ev.EligibilityStatus, ev.HasVoted, ev.VotedTimestamp
FROM ElectionVoter ev
JOIN Voter    v ON v.VoterID    = ev.VoterID
JOIN Election e ON e.ElectionID = ev.ElectionID;

-- One row per election with headline numbers (uses the functions above)
CREATE OR REPLACE VIEW v_election_summary AS
SELECT e.ElectionID, e.ElectionCode, e.ElectionName, e.ElectionType,
       e.StartDate, e.EndDate, e.Status, o.Name AS OfficerName,
       (SELECT COUNT(*) FROM ElectionVoter ev
         WHERE ev.ElectionID = e.ElectionID AND ev.EligibilityStatus = 'Eligible') AS EligibleVoters,
       (SELECT COUNT(*) FROM Vote vt WHERE vt.ElectionID = e.ElectionID)            AS VotesCast,
       (SELECT COUNT(*) FROM CandidateNomination n
         WHERE n.ElectionID = e.ElectionID AND n.Status = 'Approved')               AS ApprovedCandidates,
       (SELECT COUNT(*) FROM CandidateNomination n
         WHERE n.ElectionID = e.ElectionID AND n.Status = 'Pending')                AS PendingNominations,
       fn_turnout_percent(e.ElectionID) AS TurnoutPercent
FROM Election e
JOIN ElectionOfficer o ON o.OfficerID = e.OfficerID;

-- Published results (aggregate only - never exposes who voted for whom)
CREATE OR REPLACE VIEW v_public_results AS
SELECT er.ElectionID, e.ElectionCode, e.ElectionName,
       co.ConstituencyID, co.ConstituencyName,
       ca.CandidateID, ca.Name AS CandidateName, p.PartyName, p.Symbol,
       er.VoteCount, er.ResultStatus
FROM ElectionResult er
JOIN Election             e  ON e.ElectionID      = er.ElectionID
JOIN Candidate            ca ON ca.CandidateID    = er.CandidateID
JOIN PoliticalParty       p  ON p.PartyID         = ca.PartyID
JOIN CandidateNomination  n  ON n.ElectionID = er.ElectionID AND n.CandidateID = er.CandidateID
JOIN Constituency         co ON co.ConstituencyID = n.ConstituencyID;

/* --------------------------------------------------------------------------
   4. STORED PROCEDURES
   -------------------------------------------------------------------------- */
DROP PROCEDURE IF EXISTS sp_cast_vote;
DROP PROCEDURE IF EXISTS sp_close_election_and_generate_results;
DROP PROCEDURE IF EXISTS sp_assign_constituency_voters;

DELIMITER $$

/* sp_cast_vote : the ONLY way the application records a vote.
   One transaction: validate -> lock the voter's ElectionVoter row -> insert Vote.
   Triggers then enforce the business rules and mark HasVoted. Any failure rolls back. */
CREATE PROCEDURE sp_cast_vote(IN p_voter INT, IN p_election INT, IN p_candidate INT, OUT p_vote_id INT)
BEGIN
    DECLARE v_status VARCHAR(20) DEFAULT NULL;
    DECLARE v_elig   VARCHAR(20) DEFAULT NULL;
    DECLARE v_voted  TINYINT     DEFAULT NULL;
    DECLARE v_got    INT         DEFAULT 0;
    DECLARE v_locked TINYINT     DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        IF v_locked = 1 THEN
            DO RELEASE_LOCK('evoting_vote_id');
        END IF;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT Status INTO v_status FROM Election WHERE ElectionID = p_election;
    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election does not exist';
    END IF;
    IF v_status <> 'Active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election is not open for voting';
    END IF;

    SELECT EligibilityStatus, HasVoted INTO v_elig, v_voted
      FROM ElectionVoter
     WHERE ElectionID = p_election AND VoterID = p_voter
       FOR UPDATE;
    IF v_elig IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter is not registered for this election';
    END IF;
    IF v_elig <> 'Eligible' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter is not eligible for this election';
    END IF;
    IF v_voted = 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'You have already voted in this election';
    END IF;

    -- VoteID has no AUTO_INCREMENT in evoting, so generate the next id under a named lock
    SELECT GET_LOCK('evoting_vote_id', 10) INTO v_got;
    IF v_got <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'System busy, please try again';
    END IF;
    SET v_locked = 1;

    SELECT COALESCE(MAX(VoteID), 0) + 1 INTO p_vote_id FROM Vote;
    INSERT INTO Vote (VoteID, VoterID, ElectionID, CandidateID, VoteTime)
    VALUES (p_vote_id, p_voter, p_election, p_candidate, NOW());

    COMMIT;
    DO RELEASE_LOCK('evoting_vote_id');
END$$

/* sp_close_election_and_generate_results : Active -> Closed, then fill ElectionResult.
   Winner = highest votes within each constituency (RANK), ties share the win,
   a constituency with no votes has no winner. Candidates with 0 votes are listed too. */
CREATE PROCEDURE sp_close_election_and_generate_results(IN p_election INT, OUT p_rows INT)
BEGIN
    DECLARE v_status VARCHAR(20) DEFAULT NULL;
    DECLARE v_max    INT         DEFAULT 0;
    DECLARE v_got    INT         DEFAULT 0;
    DECLARE v_locked TINYINT     DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        IF v_locked = 1 THEN
            DO RELEASE_LOCK('evoting_result_id');
        END IF;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT Status INTO v_status FROM Election WHERE ElectionID = p_election FOR UPDATE;
    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election does not exist';
    END IF;
    IF v_status <> 'Active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only an Active election can be closed';
    END IF;
    IF EXISTS (SELECT 1 FROM ElectionResult WHERE ElectionID = p_election) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Results already exist for this election';
    END IF;

    SELECT GET_LOCK('evoting_result_id', 10) INTO v_got;
    IF v_got <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'System busy, please try again';
    END IF;
    SET v_locked = 1;

    SELECT COALESCE(MAX(ResultID), 0) INTO v_max FROM ElectionResult;

    INSERT INTO ElectionResult (ResultID, ElectionID, CandidateID, VoteCount, ResultStatus)
    SELECT v_max + ROW_NUMBER() OVER (ORDER BY t.ConstituencyID, t.cnt DESC, t.CandidateID),
           t.ElectionID, t.CandidateID, t.cnt,
           IF(t.rk = 1 AND t.cnt > 0, 'Winner', 'Non-winner')
    FROM (
        SELECT n.ElectionID, n.CandidateID, n.ConstituencyID,
               COUNT(v.VoteID) AS cnt,
               RANK() OVER (PARTITION BY n.ConstituencyID ORDER BY COUNT(v.VoteID) DESC) AS rk
        FROM CandidateNomination n
        LEFT JOIN Vote v ON v.ElectionID = n.ElectionID AND v.CandidateID = n.CandidateID
        WHERE n.ElectionID = p_election AND n.Status = 'Approved'
        GROUP BY n.ElectionID, n.CandidateID, n.ConstituencyID
    ) t;
    SET p_rows = ROW_COUNT();

    UPDATE Election SET Status = 'Closed' WHERE ElectionID = p_election;

    COMMIT;
    DO RELEASE_LOCK('evoting_result_id');
END$$

/* sp_assign_constituency_voters : make every Active voter of a constituency part of
   an election (Eligible).  Voters already assigned are skipped. */
CREATE PROCEDURE sp_assign_constituency_voters(IN p_election INT, IN p_constituency INT, OUT p_added INT)
BEGIN
    DECLARE v_status VARCHAR(20) DEFAULT NULL;
    DECLARE v_max    INT         DEFAULT 0;
    DECLARE v_got    INT         DEFAULT 0;
    DECLARE v_locked TINYINT     DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        IF v_locked = 1 THEN
            DO RELEASE_LOCK('evoting_ev_id');
        END IF;
        RESIGNAL;
    END;

    SELECT Status INTO v_status FROM Election WHERE ElectionID = p_election;
    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election does not exist';
    END IF;
    IF v_status = 'Closed' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot add voters to a closed election';
    END IF;

    START TRANSACTION;
    SELECT GET_LOCK('evoting_ev_id', 10) INTO v_got;
    IF v_got <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'System busy, please try again';
    END IF;
    SET v_locked = 1;

    SELECT COALESCE(MAX(ElectionVoterID), 0) INTO v_max FROM ElectionVoter;

    INSERT INTO ElectionVoter (ElectionVoterID, ElectionID, VoterID, EligibilityStatus, HasVoted, VotedTimestamp)
    SELECT v_max + ROW_NUMBER() OVER (ORDER BY vo.VoterID), p_election, vo.VoterID, 'Eligible', 0, NULL
    FROM Voter vo
    WHERE vo.ConstituencyID = p_constituency
      AND vo.AccountStatus  = 'Active'
      AND NOT EXISTS (SELECT 1 FROM ElectionVoter x
                      WHERE x.ElectionID = p_election AND x.VoterID = vo.VoterID);
    SET p_added = ROW_COUNT();

    COMMIT;
    DO RELEASE_LOCK('evoting_ev_id');
END$$

DELIMITER ;

/* --------------------------------------------------------------------------
   5. TRIGGERS  (trg_vote_business_rules from schema.sql is kept as it is)
   -------------------------------------------------------------------------- */
DROP TRIGGER IF EXISTS trg_vote_election_open;
DROP TRIGGER IF EXISTS trg_vote_after_insert;
DROP TRIGGER IF EXISTS trg_election_status_guard;
DROP TRIGGER IF EXISTS trg_election_audit;
DROP TRIGGER IF EXISTS trg_nomination_rules;
DROP TRIGGER IF EXISTS trg_nomination_audit;
DROP TRIGGER IF EXISTS trg_voter_delete_audit;

DELIMITER $$

-- A vote is only accepted while the election is Active and the voter's account is Active
CREATE TRIGGER trg_vote_election_open
BEFORE INSERT ON Vote
FOR EACH ROW
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Election
                   WHERE ElectionID = NEW.ElectionID AND Status = 'Active') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election is not open for voting';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM Voter
                   WHERE VoterID = NEW.VoterID AND AccountStatus = 'Active') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter account is not active';
    END IF;
END$$

-- After a vote: mark the voter as having voted and write an audit line
-- (the audit line deliberately does NOT record the candidate - ballot secrecy)
CREATE TRIGGER trg_vote_after_insert
AFTER INSERT ON Vote
FOR EACH ROW
BEGIN
    UPDATE ElectionVoter
       SET HasVoted = 1, VotedTimestamp = NEW.VoteTime
     WHERE ElectionID = NEW.ElectionID AND VoterID = NEW.VoterID;
    INSERT INTO AuditLog (TableName, Action, RecordID, Details)
    VALUES ('Vote', 'VOTE_CAST', NEW.VoteID,
            CONCAT('Voter ', NEW.VoterID, ' voted in election ', NEW.ElectionID));
END$$

-- A Closed election is final; an election that already has votes cannot go back to Upcoming
CREATE TRIGGER trg_election_status_guard
BEFORE UPDATE ON Election
FOR EACH ROW
BEGIN
    IF OLD.Status = 'Closed' AND NEW.Status <> 'Closed' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A closed election cannot be re-opened';
    END IF;
    IF NEW.Status = 'Upcoming' AND OLD.Status = 'Active'
       AND EXISTS (SELECT 1 FROM Vote WHERE ElectionID = OLD.ElectionID) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Votes already cast; election cannot go back to Upcoming';
    END IF;
END$$

CREATE TRIGGER trg_election_audit
AFTER UPDATE ON Election
FOR EACH ROW
BEGIN
    IF OLD.Status <> NEW.Status THEN
        INSERT INTO AuditLog (TableName, Action, RecordID, Details)
        VALUES ('Election', 'STATUS_CHANGE', NEW.ElectionID,
                CONCAT(NEW.ElectionCode, ': ', OLD.Status, ' -> ', NEW.Status));
    END IF;
END$$

-- Nominations: not for closed elections, candidate account must be Active
CREATE TRIGGER trg_nomination_rules
BEFORE INSERT ON CandidateNomination
FOR EACH ROW
BEGIN
    IF EXISTS (SELECT 1 FROM Election WHERE ElectionID = NEW.ElectionID AND Status = 'Closed') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nominations are closed for this election';
    END IF;
    IF EXISTS (SELECT 1 FROM Candidate WHERE CandidateID = NEW.CandidateID AND AccountStatus <> 'Active') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Candidate account is not active';
    END IF;
END$$

CREATE TRIGGER trg_nomination_audit
AFTER UPDATE ON CandidateNomination
FOR EACH ROW
BEGIN
    IF OLD.Status <> NEW.Status THEN
        INSERT INTO AuditLog (TableName, Action, RecordID, Details)
        VALUES ('CandidateNomination', 'STATUS_CHANGE', NEW.NominationID,
                CONCAT('Nomination ', NEW.NominationID, ': ', OLD.Status, ' -> ', NEW.Status));
    END IF;
END$$

CREATE TRIGGER trg_voter_delete_audit
AFTER DELETE ON Voter
FOR EACH ROW
BEGIN
    INSERT INTO AuditLog (TableName, Action, RecordID, Details)
    VALUES ('Voter', 'DELETE', OLD.VoterID, CONCAT('Voter removed: ', OLD.Username));
END$$

DELIMITER ;

SELECT 'Application objects installed' AS Status;
