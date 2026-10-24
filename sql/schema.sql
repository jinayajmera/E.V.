/* ==========================================================================
   E-VOTING : BASE SCHEMA AND SAMPLE DATA

   Creates the database `evoting`, the 10 core tables and a small set of
   sample records (constituencies, parties, officers, voters, candidates,
   elections, votes).

   Run this ONCE on a new, empty MySQL 8.0.31+ server, before anything else:
       mysql -u root -p < sql/schema.sql
   Then run:  python scripts/upgrade_database.py
   It never drops an existing database. If `evoting` already has tables,
   skip this file and go straight to the upgrade script.
   ========================================================================== */

CREATE DATABASE IF NOT EXISTS evoting;
USE evoting;

-- 1. Constituency ----------------------------------------------------------
CREATE TABLE Constituency (
    ConstituencyID    INT AUTO_INCREMENT PRIMARY KEY,
    ConstituencyName  VARCHAR(100) NOT NULL,
    State             VARCHAR(100) NOT NULL,
    District          VARCHAR(100) NOT NULL,
    CONSTRAINT uq_constituency UNIQUE (ConstituencyName, State, District)
);

-- 2. PoliticalParty --------------------------------------------------------
CREATE TABLE PoliticalParty (
    PartyID      INT AUTO_INCREMENT PRIMARY KEY,
    PartyName    VARCHAR(100) NOT NULL UNIQUE,
    Symbol       VARCHAR(100) NOT NULL,
    FoundedYear  INT,
    Description  VARCHAR(255),
    CONSTRAINT chk_party_year CHECK (FoundedYear BETWEEN 1800 AND 2100)
);

-- 3. ElectionOfficer -------------------------------------------------------
CREATE TABLE ElectionOfficer (
    OfficerID      INT AUTO_INCREMENT PRIMARY KEY,
    Name           VARCHAR(100) NOT NULL,
    Email          VARCHAR(100) NOT NULL UNIQUE,
    Phone          VARCHAR(15)  NOT NULL,
    Username       VARCHAR(50)  NOT NULL UNIQUE,
    Password       VARCHAR(255) NOT NULL,
    AccountStatus  VARCHAR(20)  NOT NULL DEFAULT 'Active',
    CONSTRAINT chk_officer_status CHECK (AccountStatus IN ('Active','Suspended','Inactive')),
    CONSTRAINT chk_officer_email  CHECK (Email LIKE '%_@_%._%')
);

-- 4. Voter -----------------------------------------------------------------
CREATE TABLE Voter (
    VoterID           INT AUTO_INCREMENT PRIMARY KEY,
    Username          VARCHAR(50)  NOT NULL UNIQUE,
    Password          VARCHAR(255) NOT NULL,
    AccountStatus     VARCHAR(20)  NOT NULL DEFAULT 'Active',
    Name              VARCHAR(100) NOT NULL,
    DOB               DATE         NOT NULL,
    Gender            VARCHAR(20)  NOT NULL,
    Address           VARCHAR(255),
    Email             VARCHAR(100) NOT NULL UNIQUE,
    Phone             VARCHAR(15),
    RegistrationDate  DATE         NOT NULL DEFAULT (CURDATE()),
    ConstituencyID    INT          NOT NULL,
    CONSTRAINT fk_voter_constituency FOREIGN KEY (ConstituencyID)
        REFERENCES Constituency(ConstituencyID)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_voter_status CHECK (AccountStatus IN ('Active','Suspended','Inactive')),
    CONSTRAINT chk_voter_gender CHECK (Gender IN ('Male','Female','Other')),
    CONSTRAINT chk_voter_email  CHECK (Email LIKE '%_@_%._%'),
    CONSTRAINT chk_voter_age    CHECK (RegistrationDate >= DATE_ADD(DOB, INTERVAL 18 YEAR))
);

-- 5. Election --------------------------------------------------------------
CREATE TABLE Election (
    ElectionID    INT AUTO_INCREMENT PRIMARY KEY,
    ElectionCode  VARCHAR(30)  NOT NULL UNIQUE,
    ElectionName  VARCHAR(100) NOT NULL,
    Description   VARCHAR(255),
    ElectionType  VARCHAR(50)  NOT NULL,
    StartDate     DATE         NOT NULL,
    EndDate       DATE         NOT NULL,
    Status        VARCHAR(20)  NOT NULL DEFAULT 'Upcoming',
    OfficerID     INT          NOT NULL,
    CONSTRAINT fk_election_officer FOREIGN KEY (OfficerID)
        REFERENCES ElectionOfficer(OfficerID)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_election_status CHECK (Status IN ('Upcoming','Active','Closed')),
    CONSTRAINT chk_election_type   CHECK (ElectionType IN ('General','State','Bypoll','Local')),
    CONSTRAINT chk_election_dates  CHECK (EndDate >= StartDate)
);

-- 6. Candidate -------------------------------------------------------------
CREATE TABLE Candidate (
    CandidateID    INT AUTO_INCREMENT PRIMARY KEY,
    Username       VARCHAR(50)  NOT NULL UNIQUE,
    Password       VARCHAR(255) NOT NULL,
    AccountStatus  VARCHAR(20)  NOT NULL DEFAULT 'Active',
    Name           VARCHAR(100) NOT NULL,
    DOB            DATE         NOT NULL,
    Gender         VARCHAR(20)  NOT NULL,
    PartyID        INT          NOT NULL,
    Photo          VARCHAR(255) DEFAULT 'default.png',
    CONSTRAINT fk_candidate_party FOREIGN KEY (PartyID)
        REFERENCES PoliticalParty(PartyID)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_cand_status CHECK (AccountStatus IN ('Active','Suspended','Inactive')),
    CONSTRAINT chk_cand_gender CHECK (Gender IN ('Male','Female','Other'))
);

-- 7. CandidateNomination (resolves Election M:N Candidate) -----------------
CREATE TABLE CandidateNomination (
    NominationID    INT AUTO_INCREMENT PRIMARY KEY,
    CandidateID     INT         NOT NULL,
    ElectionID      INT         NOT NULL,
    ConstituencyID  INT         NOT NULL,
    NominationDate  DATE        NOT NULL,
    Status          VARCHAR(20) NOT NULL DEFAULT 'Pending',
    CONSTRAINT fk_nom_candidate    FOREIGN KEY (CandidateID)
        REFERENCES Candidate(CandidateID)       ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_nom_election     FOREIGN KEY (ElectionID)
        REFERENCES Election(ElectionID)         ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_nom_constituency FOREIGN KEY (ConstituencyID)
        REFERENCES Constituency(ConstituencyID) ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT uq_nomination UNIQUE (CandidateID, ElectionID),
    CONSTRAINT chk_nom_status CHECK (Status IN ('Approved','Rejected','Pending'))
);

-- 8. ElectionVoter (resolves Election M:N Voter) ---------------------------
CREATE TABLE ElectionVoter (
    ElectionVoterID    INT AUTO_INCREMENT PRIMARY KEY,
    ElectionID         INT         NOT NULL,
    VoterID            INT         NOT NULL,
    EligibilityStatus  VARCHAR(20) NOT NULL DEFAULT 'Eligible',
    HasVoted           BOOLEAN     NOT NULL DEFAULT FALSE,
    VotedTimestamp     DATETIME    NULL,
    CONSTRAINT fk_ev_election FOREIGN KEY (ElectionID)
        REFERENCES Election(ElectionID) ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_ev_voter    FOREIGN KEY (VoterID)
        REFERENCES Voter(VoterID)       ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT uq_election_voter UNIQUE (ElectionID, VoterID),
    CONSTRAINT chk_ev_elig   CHECK (EligibilityStatus IN ('Eligible','Not Eligible')),
    CONSTRAINT chk_ev_voted  CHECK ((HasVoted = 0 AND VotedTimestamp IS NULL)
                                 OR (HasVoted = 1 AND VotedTimestamp IS NOT NULL))
);

-- 9. Vote ------------------------------------------------------------------
CREATE TABLE Vote (
    VoteID       INT AUTO_INCREMENT PRIMARY KEY,
    VoterID      INT      NOT NULL,
    ElectionID   INT      NOT NULL,
    CandidateID  INT      NOT NULL,
    VoteTime     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_vote_voter     FOREIGN KEY (VoterID)
        REFERENCES Voter(VoterID)         ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_vote_election  FOREIGN KEY (ElectionID)
        REFERENCES Election(ElectionID)   ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_vote_candidate FOREIGN KEY (CandidateID)
        REFERENCES Candidate(CandidateID) ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT uq_one_vote_per_election UNIQUE (VoterID, ElectionID)
);

-- 10. ElectionResult -------------------------------------------------------
CREATE TABLE ElectionResult (
    ResultID      INT AUTO_INCREMENT PRIMARY KEY,
    ElectionID    INT         NOT NULL,
    CandidateID   INT         NOT NULL,
    VoteCount     INT         NOT NULL DEFAULT 0,
    ResultStatus  VARCHAR(20) NOT NULL DEFAULT 'Non-winner',
    CONSTRAINT fk_res_election  FOREIGN KEY (ElectionID)
        REFERENCES Election(ElectionID)   ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_res_candidate FOREIGN KEY (CandidateID)
        REFERENCES Candidate(CandidateID) ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT uq_result UNIQUE (ElectionID, CandidateID),
    CONSTRAINT chk_res_status CHECK (ResultStatus IN ('Winner','Non-winner')),
    CONSTRAINT chk_res_count  CHECK (VoteCount >= 0)
);



/* ==========================================================================
   SECTION 3A : DATA MANIPULATION  -  INSERT (sample data)
   ========================================================================== */

INSERT INTO Constituency (ConstituencyName, State, District) VALUES
 ('Vellore Central', 'Tamil Nadu',  'Vellore'),
 ('Chennai North',   'Tamil Nadu',  'Chennai'),
 ('Mumbai South',    'Maharashtra', 'Mumbai'),
 ('Pune East',       'Maharashtra', 'Pune');

INSERT INTO PoliticalParty (PartyName, Symbol, FoundedYear, Description) VALUES
 ('Progress Alliance',  'Sun',  1985, 'Focus on infrastructure and development'),
 ('Unity Front',        'Tree', 1999, 'Focus on social welfare and unity'),
 ('Green Future Party', 'Star', 2008, 'Focus on environment and clean energy'),
 ('Peoples Voice',      'Bell', 2015, 'Focus on youth and education');

INSERT INTO ElectionOfficer (Name, Email, Phone, Username, Password, AccountStatus) VALUES
 ('Rajesh Kumar', 'rajesh.kumar@eci.gov.in', '9876500001', 'rajesh_off', SHA2('officer123', 256), 'Active'),
 ('Anita Sharma', 'anita.sharma@eci.gov.in', '9876500002', 'anita_off',  SHA2('officer123', 256), 'Active'),
 ('Suresh Iyer',  'suresh.iyer@eci.gov.in',  '9876500003', 'suresh_off', SHA2('officer123', 256), 'Active'),
 ('Meera Nair',   'meera.nair@eci.gov.in',   '9876500004', 'meera_off',  SHA2('officer123', 256), 'Inactive');

INSERT INTO Voter (Username, Password, AccountStatus, Name, DOB, Gender, Address, Email, Phone, RegistrationDate, ConstituencyID) VALUES
 ('aarav01',  SHA2('pass123',256), 'Active',   'Aarav Sharma',    '2000-05-14', 'Male',   '12 Gandhi Road, Vellore',     'aarav@gmail.com',    '9000000001', '2022-01-10', 1),
 ('priya02',  SHA2('pass123',256), 'Active',   'Priya Nair',      '1998-11-02', 'Female', '45 Lake View, Vellore',       'priya@gmail.com',    '9000000002', '2021-06-15', 1),
 ('karthik03',SHA2('pass123',256), 'Active',   'Karthik Raman',   '1995-03-21', 'Male',   '8 Temple St, Katpadi',        'karthik@yahoo.com',  '9000000003', '2020-02-20', 1),
 ('ananya04', SHA2('pass123',256), 'Active',   'Ananya Iyer',     '2001-07-09', 'Female', '77 Anna Nagar, Chennai',      'ananya@gmail.com',   '9000000004', '2023-03-05', 2),
 ('farhan05', SHA2('pass123',256), 'Active',   'Mohammed Farhan', '1990-12-30', 'Male',   '21 Mount Road, Chennai',      'farhan@outlook.com', '9000000005', '2019-08-18', 2),
 ('lakshmi06',SHA2('pass123',256), 'Active',   'Lakshmi Devi',    '1985-09-17', 'Female', '5 T Nagar, Chennai',          'lakshmi@gmail.com',  '9000000006', '2018-01-25', 2),
 ('rahul07',  SHA2('pass123',256), 'Active',   'Rahul Deshmukh',  '1992-04-12', 'Male',   '9 Marine Drive, Mumbai',      'rahul@gmail.com',    '9000000007', '2020-11-11', 3),
 ('neha08',   SHA2('pass123',256), 'Inactive', 'Neha Kulkarni',   '2002-01-25', 'Female', '33 FC Road, Vellore',         'neha@gmail.com',     '9000000008', '2024-01-05', 1);

INSERT INTO Election (ElectionCode, ElectionName, Description, ElectionType, StartDate, EndDate, Status, OfficerID) VALUES
 ('GEN2024', 'General Election 2024', 'National general election',      'General', '2024-04-10', '2024-04-12', 'Closed',   1),
 ('STE2025', 'State Election 2025',   'State assembly election',        'State',   '2025-05-15', '2025-05-16', 'Closed',   2),
 ('GEN2026', 'General Election 2026', 'National general election',      'General', '2026-09-25', '2026-10-05', 'Active',   3),
 ('BYE2026', 'Bypoll December 2026',  'By-election for vacant seat',    'Bypoll',  '2026-12-01', '2026-12-02', 'Upcoming', 4);

INSERT INTO Candidate (Username, Password, AccountStatus, Name, DOB, Gender, PartyID, Photo) VALUES
 ('arjun_c',  SHA2('cand123',256), 'Active', 'Arjun Mehta',  '1975-02-10', 'Male',   1, 'arjun.jpg'),
 ('kavita_c', SHA2('cand123',256), 'Active', 'Kavita Reddy', '1980-08-19', 'Female', 2, 'kavita.jpg'),
 ('rohan_c',  SHA2('cand123',256), 'Active', 'Rohan Das',    '1978-06-30', 'Male',   3, 'rohan.jpg'),
 ('sneha_c',  SHA2('cand123',256), 'Active', 'Sneha Pillai', '1985-12-05', 'Female', 4, 'sneha.jpg'),
 ('vikram_c', SHA2('cand123',256), 'Active', 'Vikram Singh', '1970-09-23', 'Male',   1, 'vikram.jpg'),
 ('divya_c',  SHA2('cand123',256), 'Active', 'Divya Menon',  '1983-03-14', 'Female', 2, 'divya.jpg');

INSERT INTO CandidateNomination (CandidateID, ElectionID, ConstituencyID, NominationDate, Status) VALUES
 (1, 1, 1, '2024-03-01', 'Approved'),
 (2, 1, 1, '2024-03-02', 'Approved'),
 (3, 1, 2, '2024-03-02', 'Approved'),
 (4, 1, 2, '2024-03-03', 'Approved'),
 (5, 2, 1, '2025-04-01', 'Approved'),
 (6, 2, 1, '2025-04-02', 'Approved'),
 (1, 3, 1, '2026-08-10', 'Approved'),
 (2, 3, 1, '2026-08-11', 'Approved'),
 (5, 3, 1, '2026-08-12', 'Pending'),
 (3, 3, 2, '2026-08-11', 'Approved'),
 (4, 3, 2, '2026-08-12', 'Approved'),
 (6, 3, 2, '2026-08-13', 'Rejected');

-- Election 1 (voters 1-6 eligible), Election 2, Election 3, Election 4
INSERT INTO ElectionVoter (ElectionID, VoterID, EligibilityStatus) VALUES
 (1,1,'Eligible'),(1,2,'Eligible'),(1,3,'Eligible'),(1,4,'Eligible'),(1,5,'Eligible'),(1,6,'Eligible'),
 (2,1,'Eligible'),(2,2,'Eligible'),(2,3,'Eligible'),(2,4,'Not Eligible'),
 (3,1,'Eligible'),(3,2,'Eligible'),(3,3,'Eligible'),(3,4,'Eligible'),(3,5,'Eligible'),(3,6,'Eligible'),(3,7,'Eligible'),(3,8,'Not Eligible'),
 (4,1,'Eligible'),(4,2,'Eligible');

INSERT INTO Vote (VoterID, ElectionID, CandidateID, VoteTime) VALUES
 (1, 1, 1, '2024-04-10 09:15:00'),
 (2, 1, 1, '2024-04-10 10:20:00'),
 (3, 1, 2, '2024-04-10 11:05:00'),
 (4, 1, 3, '2024-04-11 09:40:00'),
 (5, 1, 3, '2024-04-11 14:30:00'),
 (6, 1, 4, '2024-04-12 10:10:00'),
 (1, 2, 5, '2025-05-15 09:00:00'),
 (2, 2, 6, '2025-05-15 10:30:00'),
 (3, 2, 5, '2025-05-16 11:45:00'),
 (1, 3, 1, '2026-09-26 09:30:00'),
 (2, 3, 2, '2026-09-26 12:15:00'),
 (4, 3, 3, '2026-09-27 10:05:00'),
 (5, 3, 3, '2026-09-27 15:20:00');

-- Sync ElectionVoter with recorded votes (multi-table UPDATE)
UPDATE ElectionVoter ev
JOIN Vote v ON v.ElectionID = ev.ElectionID AND v.VoterID = ev.VoterID
SET ev.HasVoted = TRUE, ev.VotedTimestamp = v.VoteTime;

-- Generate results for CLOSED elections (VoteCount is derived from Vote table)
INSERT INTO ElectionResult (ElectionID, CandidateID, VoteCount, ResultStatus)
SELECT t.ElectionID, t.CandidateID, t.cnt,
       IF(RANK() OVER (PARTITION BY t.ElectionID, t.ConstituencyID ORDER BY t.cnt DESC) = 1,
          'Winner', 'Non-winner')
FROM (
    SELECT v.ElectionID, v.CandidateID, cn.ConstituencyID, COUNT(*) AS cnt
    FROM Vote v
    JOIN CandidateNomination cn ON cn.ElectionID = v.ElectionID AND cn.CandidateID = v.CandidateID
    JOIN Election e ON e.ElectionID = v.ElectionID
    WHERE e.Status = 'Closed'
    GROUP BY v.ElectionID, v.CandidateID, cn.ConstituencyID
) t;


/* ==========================================================================
   BUSINESS-RULE TRIGGER on Vote
   - the voter must be marked Eligible for the election
   - the candidate must have an Approved nomination in the voter's constituency
   One vote per voter per election is enforced by UNIQUE (VoterID, ElectionID).
   ========================================================================== */
DELIMITER $$
CREATE TRIGGER trg_vote_business_rules
BEFORE INSERT ON Vote
FOR EACH ROW
BEGIN
    -- Rule 2: voter must be marked Eligible for this election
    IF NOT EXISTS (SELECT 1 FROM ElectionVoter
                   WHERE ElectionID = NEW.ElectionID
                     AND VoterID = NEW.VoterID
                     AND EligibilityStatus = 'Eligible') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Voter is not eligible for this election';
    END IF;

    -- Rules 1 and 4: candidate must have an APPROVED nomination in the voter's constituency
    IF NOT EXISTS (SELECT 1
                   FROM CandidateNomination cn
                   JOIN Voter vo ON vo.ConstituencyID = cn.ConstituencyID
                   WHERE cn.CandidateID = NEW.CandidateID
                     AND cn.ElectionID  = NEW.ElectionID
                     AND cn.Status      = 'Approved'
                     AND vo.VoterID     = NEW.VoterID) THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Candidate is not approved in this voter''s constituency for this election';
    END IF;
END$$
DELIMITER ;
