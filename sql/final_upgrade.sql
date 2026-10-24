/* ==========================================================================
   FINAL APPLICATION UPGRADE  (identity security, voter photos, ACID support)
   Database : the one named in your .env   (MySQL 8.0.31+)

   SAFE TO RUN :  * It never drops or empties a table, and never deletes a row.
                  * Existing tables only receive NEW columns, indexes and checks.
                  * Existing voters keep working: they are marked "Verified" once.
                  * Only objects created by the project itself (views, functions,
                    procedures, triggers) are re-created, so it can be run again
                    and again.
   HOW TO RUN  :  python scripts/upgrade_database.py      (recommended - also
                  makes a JSON safety copy first and seeds the sample identity data)
                  or MySQL Workbench -> open this file -> run the whole script.
   ========================================================================== */

USE evoting;

/* --------------------------------------------------------------------------
   0. Helper procedures (removed again at the end of the script)
   -------------------------------------------------------------------------- */
DROP PROCEDURE IF EXISTS sp_up_exec;
DROP PROCEDURE IF EXISTS sp_up_add_column;
DROP PROCEDURE IF EXISTS sp_up_add_index;
DROP PROCEDURE IF EXISTS sp_up_add_check;
DROP PROCEDURE IF EXISTS sp_up_add_fk;
DROP PROCEDURE IF EXISTS sp_up_backfill;

DELIMITER $$

CREATE PROCEDURE sp_up_exec(IN p_sql TEXT)
BEGIN
    SET @up_sql = p_sql;
    PREPARE up_stmt FROM @up_sql;
    EXECUTE up_stmt;
    DEALLOCATE PREPARE up_stmt;
END$$

CREATE PROCEDURE sp_up_add_column(IN p_table VARCHAR(64), IN p_col VARCHAR(64), IN p_def TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND table_name = p_table AND column_name = p_col) THEN
        CALL sp_up_exec(CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_col, '` ', p_def));
    END IF;
END$$

CREATE PROCEDURE sp_up_add_index(IN p_table VARCHAR(64), IN p_index VARCHAR(64), IN p_def TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics
                    WHERE table_schema = DATABASE() AND table_name = p_table AND index_name = p_index) THEN
        CALL sp_up_exec(CONCAT('ALTER TABLE `', p_table, '` ADD ', p_def));
    END IF;
END$$

CREATE PROCEDURE sp_up_add_check(IN p_table VARCHAR(64), IN p_name VARCHAR(64), IN p_expr TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                    WHERE table_schema = DATABASE() AND table_name = p_table
                      AND constraint_name = p_name AND constraint_type = 'CHECK') THEN
        CALL sp_up_exec(CONCAT('ALTER TABLE `', p_table, '` ADD CONSTRAINT `', p_name, '` CHECK (', p_expr, ')'));
    END IF;
END$$

CREATE PROCEDURE sp_up_add_fk(IN p_table VARCHAR(64), IN p_name VARCHAR(64), IN p_def TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                    WHERE table_schema = DATABASE() AND table_name = p_table
                      AND constraint_name = p_name AND constraint_type = 'FOREIGN KEY') THEN
        CALL sp_up_exec(CONCAT('ALTER TABLE `', p_table, '` ADD CONSTRAINT `', p_name, '` ', p_def));
    END IF;
END$$

DELIMITER ;

/* --------------------------------------------------------------------------
   1. NEW COLUMNS ON Voter  (identity, photo, verification)
   -------------------------------------------------------------------------- */
CALL sp_up_add_column('Voter', 'NationalIDHash',     'CHAR(64) NULL');
CALL sp_up_add_column('Voter', 'NationalIDLast4',    'CHAR(4) NULL');
CALL sp_up_add_column('Voter', 'IdentitySource',     "VARCHAR(12) NOT NULL DEFAULT 'Registered'");
CALL sp_up_add_column('Voter', 'EmailNormalized',    'VARCHAR(150) NULL');
CALL sp_up_add_column('Voter', 'PhoneNormalized',    'VARCHAR(15) NULL');
CALL sp_up_add_column('Voter', 'PhotoFile',          'VARCHAR(120) NULL');
CALL sp_up_add_column('Voter', 'PhotoSha256',        'CHAR(64) NULL');
CALL sp_up_add_column('Voter', 'PhotoDHash',         'CHAR(16) NULL');
CALL sp_up_add_column('Voter', 'VerificationStatus', "VARCHAR(12) NOT NULL DEFAULT 'Pending'");
CALL sp_up_add_column('Voter', 'VerifiedBy',         'INT NULL');
CALL sp_up_add_column('Voter', 'VerifiedAt',         'DATETIME NULL');
CALL sp_up_add_column('Voter', 'VerificationNote',   'VARCHAR(255) NULL');
CALL sp_up_add_column('Voter', 'LastLoginAt',        'DATETIME NULL');

/* --------------------------------------------------------------------------
   2. NEW SUPPORT TABLES
   -------------------------------------------------------------------------- */
-- Security log + source for login / registration throttling
CREATE TABLE IF NOT EXISTS SecurityEvent (
    EventID    BIGINT AUTO_INCREMENT PRIMARY KEY,
    EventTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    EventType  VARCHAR(30)  NOT NULL,
    Role       VARCHAR(10)  NULL,
    Username   VARCHAR(50)  NULL,
    IpAddress  VARCHAR(45)  NULL,
    Details    VARCHAR(255) NULL,
    INDEX idx_secevt_user (EventType, Role, Username, EventTime),
    INDEX idx_secevt_ip   (EventType, IpAddress, EventTime)
) ENGINE = InnoDB;

-- Scratch table used by the transaction (ACID) demonstration - never holds real data
CREATE TABLE IF NOT EXISTS TxnScratch (
    ScratchID  INT          NOT NULL PRIMARY KEY,
    Note       VARCHAR(60)  NOT NULL,
    Qty        INT          NOT NULL,
    CONSTRAINT chk_scratch_qty CHECK (Qty >= 0)
) ENGINE = InnoDB;

/* --------------------------------------------------------------------------
   3. NORMALISING FUNCTIONS  (used by triggers, so the rule holds for every writer)
   -------------------------------------------------------------------------- */
DROP FUNCTION IF EXISTS fn_normalise_email;
DROP FUNCTION IF EXISTS fn_normalise_phone;

DELIMITER $$

-- a.b+x@Gmail.com  ->  ab@gmail.com    (case, "+tag" and gmail dots do not make a new person)
CREATE FUNCTION fn_normalise_email(p_email VARCHAR(150))
RETURNS VARCHAR(150)
DETERMINISTIC NO SQL
BEGIN
    DECLARE v_mail   VARCHAR(150);
    DECLARE v_local  VARCHAR(150);
    DECLARE v_domain VARCHAR(150);
    IF p_email IS NULL THEN
        RETURN NULL;
    END IF;
    SET v_mail   = LOWER(TRIM(p_email));
    SET v_local  = SUBSTRING_INDEX(v_mail, '@', 1);
    SET v_domain = SUBSTRING_INDEX(v_mail, '@', -1);
    IF v_domain = 'googlemail.com' THEN
        SET v_domain = 'gmail.com';
    END IF;
    SET v_local = SUBSTRING_INDEX(v_local, '+', 1);
    IF v_domain = 'gmail.com' THEN
        SET v_local = REPLACE(v_local, '.', '');
    END IF;
    RETURN CONCAT(v_local, '@', v_domain);
END$$

-- +91 98765-43210 -> 9876543210   (last 10 digits)
CREATE FUNCTION fn_normalise_phone(p_phone VARCHAR(30))
RETURNS VARCHAR(15)
DETERMINISTIC NO SQL
BEGIN
    DECLARE v_digits VARCHAR(30);
    IF p_phone IS NULL THEN
        RETURN NULL;
    END IF;
    SET v_digits = REGEXP_REPLACE(p_phone, '[^0-9]', '');
    IF v_digits = '' THEN
        RETURN NULL;
    END IF;
    RETURN RIGHT(v_digits, 10);
END$$

DELIMITER ;

/* --------------------------------------------------------------------------
   4. BACK-FILL OF EXISTING ROWS  (once)
   -------------------------------------------------------------------------- */
DROP PROCEDURE IF EXISTS sp_up_backfill;

DELIMITER $$

CREATE PROCEDURE sp_up_backfill()
BEGIN
    -- 4a. Voters that existed before this upgrade are marked Verified (one time only)
    IF NOT EXISTS (SELECT 1 FROM AuditLog WHERE TableName = 'System' AND Action = 'FINAL_UPGRADE') THEN
        UPDATE Voter
           SET VerificationStatus = 'Verified',
               VerifiedAt         = NOW(),
               VerificationNote   = 'Existing voter - verified when the system was upgraded'
         WHERE VerificationStatus = 'Pending';
    END IF;

    -- 4b. Normalised e-mail / phone for every row
    UPDATE Voter SET EmailNormalized = fn_normalise_email(Email)
     WHERE EmailNormalized IS NULL AND Email IS NOT NULL;
    UPDATE Voter SET PhoneNormalized = fn_normalise_phone(Phone)
     WHERE PhoneNormalized IS NULL AND Phone IS NOT NULL;

    -- 4c. Old rows that would break the new uniqueness rules are not deleted:
    --     e-mail aliases are flagged for the officer, repeated phones just lose the tie-break
    DROP TEMPORARY TABLE IF EXISTS tmp_dup_email;
    CREATE TEMPORARY TABLE tmp_dup_email AS
        SELECT v.VoterID, MIN(w.VoterID) AS FirstID
          FROM Voter v JOIN Voter w
            ON w.EmailNormalized = v.EmailNormalized AND w.VoterID < v.VoterID
         GROUP BY v.VoterID;
    UPDATE Voter v JOIN tmp_dup_email d ON d.VoterID = v.VoterID
       SET v.EmailNormalized  = NULL,
           v.VerificationStatus = 'Flagged',
           v.VerificationNote = CONCAT('Possible duplicate of voter #', d.FirstID, ' (same e-mail after normalising)');
    DROP TEMPORARY TABLE IF EXISTS tmp_dup_email;

    DROP TEMPORARY TABLE IF EXISTS tmp_dup_phone;
    CREATE TEMPORARY TABLE tmp_dup_phone AS
        SELECT v.VoterID
          FROM Voter v JOIN Voter w
            ON w.PhoneNormalized = v.PhoneNormalized AND w.VoterID < v.VoterID
         GROUP BY v.VoterID;
    UPDATE Voter v JOIN tmp_dup_phone d ON d.VoterID = v.VoterID
       SET v.PhoneNormalized = NULL;
    DROP TEMPORARY TABLE IF EXISTS tmp_dup_phone;

    -- 4d. remember that the upgrade has been applied
    IF NOT EXISTS (SELECT 1 FROM AuditLog WHERE TableName = 'System' AND Action = 'FINAL_UPGRADE') THEN
        INSERT INTO AuditLog (TableName, Action, RecordID, Details)
        VALUES ('System', 'FINAL_UPGRADE', NULL, 'Identity, photo and verification support installed');
    END IF;
END$$

DELIMITER ;

CALL sp_up_backfill();

/* --------------------------------------------------------------------------
   5. INDEXES, UNIQUE RULES AND CHECKS  (the database itself refuses duplicates)
   -------------------------------------------------------------------------- */
CALL sp_up_add_index('Voter', 'uq_voter_identity',   'UNIQUE INDEX uq_voter_identity (NationalIDHash)');
CALL sp_up_add_index('Voter', 'uq_voter_email_norm', 'UNIQUE INDEX uq_voter_email_norm (EmailNormalized)');
CALL sp_up_add_index('Voter', 'uq_voter_phone_norm', 'UNIQUE INDEX uq_voter_phone_norm (PhoneNormalized)');
CALL sp_up_add_index('Voter', 'uq_voter_photo_sha',  'UNIQUE INDEX uq_voter_photo_sha (PhotoSha256)');
CALL sp_up_add_index('Voter', 'idx_voter_verif',     'INDEX idx_voter_verif (VerificationStatus)');
CALL sp_up_add_check('Voter', 'chk_voter_verif',  "VerificationStatus IN ('Pending','Verified','Rejected','Flagged')");
CALL sp_up_add_check('Voter', 'chk_voter_idsrc',  "IdentitySource IN ('Registered','Imported')");
CALL sp_up_add_fk('Voter', 'fk_voter_verifiedby',
                  'FOREIGN KEY (VerifiedBy) REFERENCES ElectionOfficer(OfficerID) ON UPDATE CASCADE ON DELETE SET NULL');

/* --------------------------------------------------------------------------
   6. TRIGGERS ON Voter  (normalise, protect identity, audit)
   -------------------------------------------------------------------------- */
DROP TRIGGER IF EXISTS trg_voter_normalise_bi;
DROP TRIGGER IF EXISTS trg_voter_normalise_bu;
DROP TRIGGER IF EXISTS trg_voter_audit_ai;
DROP TRIGGER IF EXISTS trg_voter_audit_au;

DELIMITER $$

CREATE TRIGGER trg_voter_normalise_bi
BEFORE INSERT ON Voter
FOR EACH ROW
BEGIN
    SET NEW.EmailNormalized = fn_normalise_email(NEW.Email);
    SET NEW.PhoneNormalized = fn_normalise_phone(NEW.Phone);
END$$

CREATE TRIGGER trg_voter_normalise_bu
BEFORE UPDATE ON Voter
FOR EACH ROW
BEGIN
    IF NOT (NEW.Email <=> OLD.Email) THEN
        SET NEW.EmailNormalized = fn_normalise_email(NEW.Email);
    END IF;
    IF NOT (NEW.Phone <=> OLD.Phone) THEN
        SET NEW.PhoneNormalized = fn_normalise_phone(NEW.Phone);
    END IF;
    -- a registered identity number can never be swapped for another one
    IF OLD.NationalIDHash IS NOT NULL AND OLD.IdentitySource = 'Registered'
       AND NOT (NEW.NationalIDHash <=> OLD.NationalIDHash) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The identity number of a registered voter cannot be changed';
    END IF;
END$$

CREATE TRIGGER trg_voter_audit_ai
AFTER INSERT ON Voter
FOR EACH ROW
BEGIN
    INSERT INTO AuditLog (TableName, Action, RecordID, Details)
    VALUES ('Voter', 'REGISTERED', NEW.VoterID, CONCAT('Voter registered: ', NEW.Username));
END$$

CREATE TRIGGER trg_voter_audit_au
AFTER UPDATE ON Voter
FOR EACH ROW
BEGIN
    IF NOT (OLD.VerificationStatus <=> NEW.VerificationStatus) THEN
        INSERT INTO AuditLog (TableName, Action, RecordID, Details)
        VALUES ('Voter', 'VERIFICATION', NEW.VoterID,
                CONCAT(NEW.Username, ': ', OLD.VerificationStatus, ' -> ', NEW.VerificationStatus));
    END IF;
    IF NOT (OLD.AccountStatus <=> NEW.AccountStatus) THEN
        INSERT INTO AuditLog (TableName, Action, RecordID, Details)
        VALUES ('Voter', 'ACCOUNT_STATUS', NEW.VoterID,
                CONCAT(NEW.Username, ': ', OLD.AccountStatus, ' -> ', NEW.AccountStatus));
    END IF;
END$$

DELIMITER ;

/* --------------------------------------------------------------------------
   7. UPDATED RULES  (only verified, active voters can vote)
   -------------------------------------------------------------------------- */
DROP FUNCTION IF EXISTS fn_can_vote;
DROP TRIGGER  IF EXISTS trg_vote_election_open;
DROP PROCEDURE IF EXISTS sp_cast_vote;
DROP PROCEDURE IF EXISTS sp_assign_constituency_voters;
DROP PROCEDURE IF EXISTS sp_verify_voter;

DELIMITER $$

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
       AND e.Status = 'Active' AND v.AccountStatus = 'Active'
       AND v.VerificationStatus = 'Verified';
    RETURN IF(v_ok > 0, 1, 0);
END$$

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
    IF NOT EXISTS (SELECT 1 FROM Voter
                   WHERE VoterID = NEW.VoterID AND VerificationStatus = 'Verified') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter identity has not been verified';
    END IF;
END$$

/* sp_cast_vote  -  ACID version
   Atomicity   : one transaction; any failure rolls everything back (vote + flags + audit).
   Consistency : election open, voter active + verified + eligible, one vote per election,
                 candidate approved in the voter's constituency (trigger) - all checked.
   Isolation   : shared lock on the Election row (a concurrent "close election" must wait
                 for votes in progress), shared lock on the Voter row (a suspension cannot
                 slip in half-way), exclusive lock on the voter's ElectionVoter row
                 (two clicks / two devices of the same voter are serialised).
   Durability  : COMMIT on InnoDB (redo log flushed at commit).                              */
CREATE PROCEDURE sp_cast_vote(IN p_voter INT, IN p_election INT, IN p_candidate INT, OUT p_vote_id INT)
BEGIN
    DECLARE v_status VARCHAR(20) DEFAULT NULL;
    DECLARE v_acc    VARCHAR(20) DEFAULT NULL;
    DECLARE v_ver    VARCHAR(12) DEFAULT NULL;
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

    SELECT Status INTO v_status FROM Election WHERE ElectionID = p_election FOR SHARE;
    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election does not exist';
    END IF;
    IF v_status <> 'Active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Election is not open for voting';
    END IF;

    SELECT AccountStatus, VerificationStatus INTO v_acc, v_ver
      FROM Voter WHERE VoterID = p_voter FOR SHARE;
    IF v_acc IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter does not exist';
    END IF;
    IF v_acc <> 'Active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter account is not active';
    END IF;
    IF v_ver <> 'Verified' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter identity has not been verified';
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

/* sp_assign_constituency_voters : only ACTIVE and VERIFIED voters are added */
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
      AND vo.AccountStatus = 'Active'
      AND vo.VerificationStatus = 'Verified'
      AND NOT EXISTS (SELECT 1 FROM ElectionVoter x
                      WHERE x.ElectionID = p_election AND x.VoterID = vo.VoterID);
    SET p_added = ROW_COUNT();

    COMMIT;
    DO RELEASE_LOCK('evoting_ev_id');
END$$

/* sp_verify_voter : an officer decides on a voter's identity - one atomic step */
CREATE PROCEDURE sp_verify_voter(IN p_voter INT, IN p_officer INT, IN p_decision VARCHAR(12), IN p_note VARCHAR(255))
BEGIN
    DECLARE v_cur  VARCHAR(12) DEFAULT NULL;
    DECLARE v_hash CHAR(64)    DEFAULT NULL;
    DECLARE v_off  VARCHAR(20) DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    IF p_decision NOT IN ('Verified', 'Rejected', 'Flagged', 'Pending') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid verification decision';
    END IF;

    START TRANSACTION;

    SELECT AccountStatus INTO v_off FROM ElectionOfficer WHERE OfficerID = p_officer FOR SHARE;
    IF v_off IS NULL OR v_off <> 'Active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only an active election officer can verify voters';
    END IF;

    SELECT VerificationStatus, NationalIDHash INTO v_cur, v_hash
      FROM Voter WHERE VoterID = p_voter FOR UPDATE;
    IF v_cur IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter does not exist';
    END IF;
    IF p_decision = 'Verified' AND v_hash IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A voter without an identity number cannot be verified';
    END IF;

    UPDATE Voter
       SET VerificationStatus = p_decision,
           VerifiedBy         = p_officer,
           VerifiedAt         = NOW(),
           VerificationNote   = NULLIF(p_note, '')
     WHERE VoterID = p_voter;

    COMMIT;
END$$

DELIMITER ;

/* --------------------------------------------------------------------------
   8. VIEWS  (officer screens, duplicate watch, integrity checks)
   -------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_voter_card AS
SELECT v.VoterID, v.Username, v.Name, v.Gender, v.DOB, v.Email, v.Phone,
       v.AccountStatus, v.VerificationStatus, v.VerificationNote, v.VerifiedAt,
       v.ConstituencyID, co.ConstituencyName, co.District, v.RegistrationDate,
       v.NationalIDLast4, v.IdentitySource, v.PhotoFile, v.LastLoginAt,
       (SELECT COUNT(*) FROM ElectionVoter ev WHERE ev.VoterID = v.VoterID) AS ElectionsRegistered,
       (SELECT COUNT(*) FROM Vote vt WHERE vt.VoterID = v.VoterID)           AS VotesCast
FROM Voter v
JOIN Constituency co ON co.ConstituencyID = v.ConstituencyID;

CREATE OR REPLACE VIEW v_duplicate_suspects AS
SELECT a.VoterID AS VoterA, b.VoterID AS VoterB, 'Same name and date of birth' AS Reason
  FROM Voter a JOIN Voter b
    ON a.VoterID < b.VoterID AND a.DOB = b.DOB
   AND LOWER(TRIM(a.Name)) = LOWER(TRIM(b.Name))
UNION ALL
SELECT a.VoterID, b.VoterID, 'Same phone number'
  FROM Voter a JOIN Voter b
    ON a.VoterID < b.VoterID
   AND fn_normalise_phone(a.Phone) IS NOT NULL
   AND fn_normalise_phone(a.Phone) = fn_normalise_phone(b.Phone)
UNION ALL
SELECT a.VoterID, b.VoterID, 'Same e-mail after normalising'
  FROM Voter a JOIN Voter b
    ON a.VoterID < b.VoterID
   AND fn_normalise_email(a.Email) = fn_normalise_email(b.Email)
UNION ALL
SELECT a.VoterID, b.VoterID, 'Same photograph'
  FROM Voter a JOIN Voter b
    ON a.VoterID < b.VoterID AND a.PhotoSha256 IS NOT NULL AND a.PhotoSha256 = b.PhotoSha256;

-- Every row is a self-check; "Violations" must be 0 on a healthy system
CREATE OR REPLACE VIEW v_integrity_checks AS
SELECT 1 AS CheckNo, 'Votes without a matching voter-election record' AS CheckName,
       (SELECT COUNT(*) FROM Vote v
         LEFT JOIN ElectionVoter ev ON ev.ElectionID = v.ElectionID AND ev.VoterID = v.VoterID
        WHERE ev.ElectionVoterID IS NULL) AS Violations
UNION ALL
SELECT 2, 'Voter-election flag (HasVoted) differs from the vote records',
       (SELECT COUNT(*) FROM ElectionVoter ev
         WHERE ev.HasVoted <> (SELECT COUNT(*) > 0 FROM Vote v
                                WHERE v.ElectionID = ev.ElectionID AND v.VoterID = ev.VoterID))
UNION ALL
SELECT 3, 'Voters with more than one vote in the same election',
       (SELECT COUNT(*) FROM (SELECT VoterID, ElectionID FROM Vote
                               GROUP BY VoterID, ElectionID HAVING COUNT(*) > 1) d)
UNION ALL
SELECT 4, 'Votes for a candidate not approved in the voter''s constituency',
       (SELECT COUNT(*) FROM Vote v
          JOIN Voter vo ON vo.VoterID = v.VoterID
         WHERE NOT EXISTS (SELECT 1 FROM CandidateNomination n
                            WHERE n.ElectionID = v.ElectionID AND n.CandidateID = v.CandidateID
                              AND n.ConstituencyID = vo.ConstituencyID AND n.Status = 'Approved'))
UNION ALL
SELECT 5, 'Published results that do not add up to the votes cast',
       (SELECT COUNT(*) FROM Election e
         WHERE e.Status = 'Closed'
           AND EXISTS (SELECT 1 FROM ElectionResult r WHERE r.ElectionID = e.ElectionID)
           AND (SELECT COALESCE(SUM(r.VoteCount), 0) FROM ElectionResult r WHERE r.ElectionID = e.ElectionID)
               <> (SELECT COUNT(*) FROM Vote v WHERE v.ElectionID = e.ElectionID))
UNION ALL
SELECT 6, 'Voters sharing one identity number',
       (SELECT COUNT(*) FROM (SELECT NationalIDHash FROM Voter WHERE NationalIDHash IS NOT NULL
                               GROUP BY NationalIDHash HAVING COUNT(*) > 1) d)
UNION ALL
SELECT 7, 'Voters sharing one e-mail address (after normalising)',
       (SELECT COUNT(*) FROM (SELECT EmailNormalized FROM Voter WHERE EmailNormalized IS NOT NULL
                               GROUP BY EmailNormalized HAVING COUNT(*) > 1) d)
UNION ALL
SELECT 8, 'Votes cast by accounts that are not verified',
       (SELECT COUNT(*) FROM Vote v JOIN Voter vo ON vo.VoterID = v.VoterID
         WHERE vo.VerificationStatus IN ('Rejected', 'Flagged'));

/* --------------------------------------------------------------------------
   9. CLEAN-UP of the helper procedures
   -------------------------------------------------------------------------- */
DROP PROCEDURE IF EXISTS sp_up_exec;
DROP PROCEDURE IF EXISTS sp_up_add_column;
DROP PROCEDURE IF EXISTS sp_up_add_index;
DROP PROCEDURE IF EXISTS sp_up_add_check;
DROP PROCEDURE IF EXISTS sp_up_add_fk;
DROP PROCEDURE IF EXISTS sp_up_backfill;

SELECT 'Final upgrade installed' AS Status;
