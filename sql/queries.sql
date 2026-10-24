/* ==========================================================================
   E-VOTING SYSTEM : SQL QUERIES  (database: evoting)
   Q1-Q36  : the original queries, unchanged (selection, aggregates,
             GROUP BY/HAVING, nested + correlated subqueries, joins, set ops,
             date + string functions).
   Q37-Q46 : queries that use the views, functions, window
             functions and the audit log.
   The web application (Officer -> SQL reports) reads THIS file, so what you
   run in Workbench and what you show in the browser is always identical.
   Run one query at a time in Workbench (select it, press Ctrl+Enter).
   ========================================================================== */

USE evoting;

-- ============ 4.1 SELECTION AND PROJECTION ==================================

-- Q1. Active voters of Vellore Central (selection + projection)
SELECT VoterID, Name, Email, Phone
FROM Voter
WHERE ConstituencyID = 1 AND AccountStatus = 'Active';

-- Q2. Elections that are not yet closed, latest first
SELECT ElectionCode, ElectionName, ElectionType, StartDate, EndDate, Status
FROM Election
WHERE Status IN ('Upcoming','Active')
ORDER BY StartDate DESC;

-- Q3. Candidates whose name starts with 'S' or 'V', with DISTINCT party ids
SELECT DISTINCT PartyID
FROM Candidate
WHERE Name LIKE 'S%' OR Name LIKE 'V%';

-- Q4. Voters born between 1990 and 2000, ordered by age
SELECT Name, DOB, Gender
FROM Voter
WHERE DOB BETWEEN '1990-01-01' AND '2000-12-31'
ORDER BY DOB;

-- ============ 4.2 AGGREGATE FUNCTIONS =======================================

-- Q5. Total votes cast, per-election summary stats
SELECT COUNT(*) AS TotalVotes,
       COUNT(DISTINCT VoterID) AS UniqueVoters,
       MIN(VoteTime) AS FirstVote,
       MAX(VoteTime) AS LastVote
FROM Vote;

-- Q6. Average, youngest and oldest voter age
SELECT ROUND(AVG(TIMESTAMPDIFF(YEAR, DOB, CURDATE())), 1) AS AvgAge,
       MIN(TIMESTAMPDIFF(YEAR, DOB, CURDATE())) AS YoungestAge,
       MAX(TIMESTAMPDIFF(YEAR, DOB, CURDATE())) AS OldestAge
FROM Voter;

-- Q7. Total votes received by each candidate across all elections (SUM)
SELECT CandidateID, SUM(VoteCount) AS TotalVotes
FROM ElectionResult
GROUP BY CandidateID;

-- ============ 4.3 GROUP BY AND HAVING =======================================

-- Q8. Number of voters per constituency
SELECT c.ConstituencyName, COUNT(v.VoterID) AS NumVoters
FROM Constituency c
LEFT JOIN Voter v ON v.ConstituencyID = c.ConstituencyID
GROUP BY c.ConstituencyID, c.ConstituencyName;

-- Q9. Votes per candidate per election, only candidates with 2 or more votes
SELECT e.ElectionCode, ca.Name AS Candidate, COUNT(*) AS Votes
FROM Vote v
JOIN Election  e  ON e.ElectionID   = v.ElectionID
JOIN Candidate ca ON ca.CandidateID = v.CandidateID
GROUP BY e.ElectionID, e.ElectionCode, ca.CandidateID, ca.Name
HAVING COUNT(*) >= 2
ORDER BY e.ElectionCode, Votes DESC;

-- Q10. Parties fielding more than one candidate
SELECT p.PartyName, COUNT(c.CandidateID) AS NumCandidates
FROM PoliticalParty p
JOIN Candidate c ON c.PartyID = p.PartyID
GROUP BY p.PartyID, p.PartyName
HAVING COUNT(c.CandidateID) > 1;

-- Q11. Voter turnout per election (%)
SELECT e.ElectionCode,
       COUNT(*) AS EligibleVoters,
       SUM(ev.HasVoted) AS VotedCount,
       ROUND(100 * SUM(ev.HasVoted) / COUNT(*), 2) AS TurnoutPercent
FROM ElectionVoter ev
JOIN Election e ON e.ElectionID = ev.ElectionID
WHERE ev.EligibilityStatus = 'Eligible'
GROUP BY e.ElectionID, e.ElectionCode
HAVING COUNT(*) > 1;

-- ============ 4.4 NESTED AND CORRELATED SUBQUERIES ==========================

-- Q12. (Nested) Voters who have never voted in any election
SELECT VoterID, Name
FROM Voter
WHERE VoterID NOT IN (SELECT VoterID FROM Vote);

-- Q13. (Nested) Candidates whose total votes exceed the average votes per candidate-election
SELECT ca.Name, COUNT(*) AS TotalVotes
FROM Vote v
JOIN Candidate ca ON ca.CandidateID = v.CandidateID
GROUP BY ca.CandidateID, ca.Name
HAVING COUNT(*) > (SELECT AVG(x.c)
                   FROM (SELECT COUNT(*) AS c FROM Vote GROUP BY CandidateID) x);

-- Q14. (Nested) Elections managed by an officer whose account is Inactive
SELECT ElectionCode, ElectionName
FROM Election
WHERE OfficerID IN (SELECT OfficerID FROM ElectionOfficer WHERE AccountStatus = 'Inactive');

-- Q15. (Correlated) Voters who voted in more than one election
SELECT vo.VoterID, vo.Name
FROM Voter vo
WHERE (SELECT COUNT(*) FROM Vote v WHERE v.VoterID = vo.VoterID) > 1;

-- Q16. (Correlated) Top vote-getter of each election (highest VoteCount within that election)
SELECT er.ElectionID, ca.Name, er.VoteCount
FROM ElectionResult er
JOIN Candidate ca ON ca.CandidateID = er.CandidateID
WHERE er.VoteCount = (SELECT MAX(er2.VoteCount)
                      FROM ElectionResult er2
                      WHERE er2.ElectionID = er.ElectionID);

-- Q17. (Correlated, EXISTS) Elections that still have eligible voters who haven't voted
SELECT e.ElectionCode, e.Status
FROM Election e
WHERE EXISTS (SELECT 1 FROM ElectionVoter ev
              WHERE ev.ElectionID = e.ElectionID
                AND ev.EligibilityStatus = 'Eligible'
                AND ev.HasVoted = FALSE);

-- Q18. (Correlated) Voters whose age is above the average age of their own constituency
SELECT v.Name, v.ConstituencyID, TIMESTAMPDIFF(YEAR, v.DOB, CURDATE()) AS Age
FROM Voter v
WHERE TIMESTAMPDIFF(YEAR, v.DOB, CURDATE()) >
      (SELECT AVG(TIMESTAMPDIFF(YEAR, v2.DOB, CURDATE()))
       FROM Voter v2
       WHERE v2.ConstituencyID = v.ConstituencyID);

-- ============ 4.5 JOINS =====================================================

-- Q19. INNER JOIN - full ballot audit (who voted for whom, in which election)
SELECT e.ElectionCode, vo.Name AS Voter, c.ConstituencyName,
       ca.Name AS VotedFor, p.PartyName, v.VoteTime
FROM Vote v
INNER JOIN Voter          vo ON vo.VoterID        = v.VoterID
INNER JOIN Election       e  ON e.ElectionID      = v.ElectionID
INNER JOIN Candidate      ca ON ca.CandidateID    = v.CandidateID
INNER JOIN PoliticalParty p  ON p.PartyID         = ca.PartyID
INNER JOIN Constituency   c  ON c.ConstituencyID  = vo.ConstituencyID
ORDER BY e.ElectionCode, v.VoteTime;

-- Q20. LEFT JOIN - voters who have never cast any vote
SELECT vo.VoterID, vo.Name, vo.AccountStatus
FROM Voter vo
LEFT JOIN Vote v ON v.VoterID = vo.VoterID
WHERE v.VoteID IS NULL;

-- Q21. RIGHT JOIN - every constituency with its voters (Pune East has none)
SELECT c.ConstituencyName, v.Name AS VoterName
FROM Voter v
RIGHT JOIN Constituency c ON c.ConstituencyID = v.ConstituencyID
ORDER BY c.ConstituencyName;

-- Q22. RIGHT JOIN - eligible voters who have not yet voted (per election)
SELECT e.ElectionCode, vo.Name AS PendingVoter
FROM Vote v
RIGHT JOIN ElectionVoter ev ON ev.ElectionID = v.ElectionID AND ev.VoterID = v.VoterID
JOIN Voter    vo ON vo.VoterID    = ev.VoterID
JOIN Election e  ON e.ElectionID  = ev.ElectionID
WHERE v.VoteID IS NULL
  AND ev.EligibilityStatus = 'Eligible'
  AND e.Status = 'Active';

-- Q23. SELF JOIN - pairs of voters registered in the same constituency
SELECT a.Name AS Voter1, b.Name AS Voter2, c.ConstituencyName
FROM Voter a
JOIN Voter b ON a.ConstituencyID = b.ConstituencyID AND a.VoterID < b.VoterID
JOIN Constituency c ON c.ConstituencyID = a.ConstituencyID
ORDER BY c.ConstituencyName;

-- Q24. SELF JOIN - candidates belonging to the same party
SELECT c1.Name AS Candidate1, c2.Name AS Candidate2, p.PartyName
FROM Candidate c1
JOIN Candidate c2 ON c1.PartyID = c2.PartyID AND c1.CandidateID < c2.CandidateID
JOIN PoliticalParty p ON p.PartyID = c1.PartyID;

-- Q25. INNER JOIN - approved candidates on the ballot for the active election
SELECT c.ConstituencyName, ca.Name AS Candidate, p.PartyName, p.Symbol
FROM CandidateNomination cn
JOIN Election e         ON e.ElectionID     = cn.ElectionID
JOIN Candidate ca       ON ca.CandidateID   = cn.CandidateID
JOIN PoliticalParty p   ON p.PartyID        = ca.PartyID
JOIN Constituency c     ON c.ConstituencyID = cn.ConstituencyID
WHERE e.Status = 'Active' AND cn.Status = 'Approved'
ORDER BY c.ConstituencyName;

-- ============ 4.6 SET OPERATIONS ============================================

-- Q26. UNION - every person registered in the system (voters, candidates, officers)
SELECT Name, 'Voter' AS Role     FROM Voter
UNION
SELECT Name, 'Candidate'         FROM Candidate
UNION
SELECT Name, 'Officer'           FROM ElectionOfficer;

-- Q27. UNION ALL - all email addresses (voters + officers), duplicates kept
SELECT Email FROM Voter
UNION ALL
SELECT Email FROM ElectionOfficer;

-- Q28. INTERSECT - voters who voted in BOTH Election 1 and Election 3 (MySQL 8.0.31+)
SELECT VoterID FROM Vote WHERE ElectionID = 1
INTERSECT
SELECT VoterID FROM Vote WHERE ElectionID = 3;

-- Q29. EXCEPT - voters who voted in Election 1 but NOT in Election 3 (MySQL 8.0.31+)
SELECT VoterID FROM Vote WHERE ElectionID = 1
EXCEPT
SELECT VoterID FROM Vote WHERE ElectionID = 3;

-- Q28b. Same as Q28 without INTERSECT (works on any MySQL version)
SELECT DISTINCT VoterID FROM Vote
WHERE ElectionID = 1 AND VoterID IN (SELECT VoterID FROM Vote WHERE ElectionID = 3);
-- Q29b. Same as Q29 without EXCEPT (works on any MySQL version)
SELECT DISTINCT VoterID FROM Vote
WHERE ElectionID = 1 AND VoterID NOT IN (SELECT VoterID FROM Vote WHERE ElectionID = 3);

-- ============ 4.7 DATE AND STRING FUNCTIONS =================================

-- Q30. Date functions: age, registration year/month, days registered
SELECT Name,
       DOB,
       TIMESTAMPDIFF(YEAR, DOB, CURDATE())            AS Age,
       YEAR(RegistrationDate)                          AS RegYear,
       MONTHNAME(RegistrationDate)                     AS RegMonth,
       DATEDIFF(CURDATE(), RegistrationDate)           AS DaysSinceRegistration,
       DATE_FORMAT(RegistrationDate, '%d-%b-%Y')       AS FormattedRegDate
FROM Voter;

-- Q31. Date functions: election duration and days until start
SELECT ElectionCode,
       StartDate, EndDate,
       DATEDIFF(EndDate, StartDate) + 1                AS DurationDays,
       DATE_ADD(EndDate, INTERVAL 30 DAY)              AS ResultDeadline,
       DAYNAME(StartDate)                              AS StartDay
FROM Election;

-- Q32. Date function: votes cast per hour of day (peak voting hours)
SELECT HOUR(VoteTime) AS HourOfDay, COUNT(*) AS Votes
FROM Vote
GROUP BY HOUR(VoteTime)
ORDER BY Votes DESC;

-- Q33. Date function: elections in which voting happened in a given month
SELECT ElectionID, COUNT(*) AS VotesInSept2026
FROM Vote
WHERE YEAR(VoteTime) = 2026 AND MONTH(VoteTime) = 9
GROUP BY ElectionID;

-- Q34. String functions: formatted names, email domain, voter card number
SELECT VoterID,
       UPPER(Name)                                     AS NameUpper,
       LENGTH(Name)                                    AS NameLength,
       SUBSTRING_INDEX(Email, '@', -1)                 AS EmailDomain,
       CONCAT('VC', LPAD(VoterID, 5, '0'))             AS VoterCardNo,
       CONCAT(LEFT(Phone, 2), 'XXXXXX', RIGHT(Phone, 2)) AS MaskedPhone
FROM Voter;

-- Q35. String functions: initials, username cleanup, pattern search
SELECT Name,
       CONCAT(LEFT(SUBSTRING_INDEX(Name,' ',1),1), LEFT(SUBSTRING_INDEX(Name,' ',-1),1)) AS Initials,
       REPLACE(Username, '_c', '')                     AS CleanUsername,
       LOCATE('a', LOWER(Name))                        AS FirstPosOfA
FROM Candidate
WHERE LOWER(Name) LIKE '%a%';

-- Q36. Final election result summary (joins + string function)
SELECT e.ElectionName,
       c.ConstituencyName,
       CONCAT(ca.Name, ' (', p.PartyName, ')') AS Candidate,
       er.VoteCount,
       er.ResultStatus
FROM ElectionResult er
JOIN Election e              ON e.ElectionID     = er.ElectionID
JOIN Candidate ca            ON ca.CandidateID   = er.CandidateID
JOIN PoliticalParty p        ON p.PartyID        = ca.PartyID
JOIN CandidateNomination cn  ON cn.ElectionID = er.ElectionID AND cn.CandidateID = er.CandidateID
JOIN Constituency c          ON c.ConstituencyID = cn.ConstituencyID
ORDER BY e.ElectionID, c.ConstituencyName, er.VoteCount DESC;



-- ============ 4.8 Views, functions and window functions =============
-- (these need sql/objects.sql to have been run once)

-- Q37. VIEW v_election_summary - headline numbers for every election
SELECT ElectionCode, Status, EligibleVoters, VotesCast, TurnoutPercent, ApprovedCandidates, PendingNominations
FROM v_election_summary
ORDER BY ElectionID;

-- Q38. VIEW v_ballot - the ballot a voter of Vellore Central (constituency 1) sees in election 3
SELECT CandidateName, PartyName, Symbol
FROM v_ballot
WHERE ElectionID = 3 AND ConstituencyID = 1
ORDER BY CandidateName;

-- Q39. VIEW v_voter_status - elections where a voter may still vote right now
SELECT VoterName, ElectionCode, EligibilityStatus, HasVoted, fn_can_vote(VoterID, ElectionID) AS CanVoteNow
FROM v_voter_status
WHERE ElectionStatus = 'Active'
ORDER BY VoterName, ElectionCode;

-- Q40. FUNCTION fn_votes_for - live vote count per approved candidate of an election
SELECT ConstituencyName, CandidateName, PartyName, fn_votes_for(ElectionID, CandidateID) AS Votes
FROM v_ballot
WHERE ElectionID = 3
ORDER BY ConstituencyName, Votes DESC;

-- Q41. WINDOW FUNCTION RANK() - candidate ranking inside each constituency (election 1)
SELECT co.ConstituencyName, ca.Name AS Candidate, COUNT(v.VoteID) AS Votes,
       RANK() OVER (PARTITION BY co.ConstituencyID ORDER BY COUNT(v.VoteID) DESC) AS PositionInConstituency
FROM CandidateNomination n
JOIN Candidate    ca ON ca.CandidateID    = n.CandidateID
JOIN Constituency co ON co.ConstituencyID = n.ConstituencyID
LEFT JOIN Vote v ON v.ElectionID = n.ElectionID AND v.CandidateID = n.CandidateID
WHERE n.ElectionID = 1 AND n.Status = 'Approved'
GROUP BY co.ConstituencyID, co.ConstituencyName, ca.CandidateID, ca.Name
ORDER BY co.ConstituencyName, PositionInConstituency;

-- Q42. WINDOW FUNCTION - running total of votes cast over time (election 1)
SELECT VoteTime, VoterID,
       COUNT(*) OVER (ORDER BY VoteTime) AS RunningTotal
FROM Vote
WHERE ElectionID = 1
ORDER BY VoteTime;

-- Q43. VIEW v_public_results - published winners of all closed elections
SELECT ElectionCode, ConstituencyName, CandidateName, PartyName, VoteCount
FROM v_public_results
WHERE ResultStatus = 'Winner'
ORDER BY ElectionCode, ConstituencyName;

-- Q44. Subquery + aggregate - voters registered for an election but who have not voted (turnout gap)
SELECT e.ElectionCode, COUNT(*) AS NotYetVoted
FROM ElectionVoter ev
JOIN Election e ON e.ElectionID = ev.ElectionID
WHERE ev.EligibilityStatus = 'Eligible' AND ev.HasVoted = 0
  AND ev.VoterID NOT IN (SELECT VoterID FROM Vote WHERE ElectionID = ev.ElectionID)
GROUP BY e.ElectionID, e.ElectionCode;

-- Q45. Party performance - total votes won by each party across all elections (4-table join)
SELECT p.PartyName, COUNT(v.VoteID) AS TotalVotes, COUNT(DISTINCT v.ElectionID) AS ElectionsContested
FROM PoliticalParty p
LEFT JOIN Candidate ca ON ca.PartyID = p.PartyID
LEFT JOIN Vote      v  ON v.CandidateID = ca.CandidateID
GROUP BY p.PartyID, p.PartyName
ORDER BY TotalVotes DESC;

-- Q46. TRIGGER output - latest entries of the audit log written by the triggers
SELECT LogID, EventTime, TableName, Action, Details
FROM AuditLog
ORDER BY LogID DESC
LIMIT 10;
