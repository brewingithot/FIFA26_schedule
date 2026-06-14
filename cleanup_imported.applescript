-- Deletes Calendar events whose summary contains any tag in `stagesToDelete`,
-- bounded to 2026-06-10..2026-07-20. Uses a date-range query first (small
-- result set, no full-calendar scan) and wraps Calendar calls in a 10-minute
-- timeout to avoid AppleEvent -1712 errors on large iCloud accounts.

on makeDate(y, m, d)
	set out to current date
	set day of out to 1
	set year of out to y
	set month of out to m
	set day of out to d
	set hours of out to 0
	set minutes of out to 0
	set seconds of out to 0
	return out
end makeDate

on summaryHasStage(s, stageList)
	repeat with aStage in stageList
		if s contains (aStage as string) then return true
	end repeat
	return false
end summaryHasStage

-- Edit this list to control which events get deleted.
-- Tags: "(Group Stage)", "(Round of 32)", "(Round of 16)",
--       "(Quarter-final)", "(Semi-final)", "(Third Place)", "(Final)"
set stagesToDelete to {"(Group Stage)"}

set startDate to my makeDate(2026, June, 10)
set endDate to my makeDate(2026, July, 20)

set toDelete to {}
set hitCalendars to {}
set samples to {}

tell application "Calendar"
	activate
	with timeout of 600 seconds
		repeat with cal in calendars
			set calTitle to title of cal
			set candidates to {}
			try
				set candidates to (every event of cal whose start date is greater than or equal to startDate and start date is less than endDate)
			end try
			repeat with ev in candidates
				try
					set s to summary of ev
					if my summaryHasStage(s, stagesToDelete) then
						set end of toDelete to ev
						if hitCalendars does not contain calTitle then
							set end of hitCalendars to calTitle
						end if
						if (count of samples) < 5 then
							set end of samples to (s & "  [" & calTitle & "]")
						end if
					end if
				end try
			end repeat
		end repeat
	end timeout
end tell

set n to count of toDelete

if n is 0 then
	set stagesText to ""
	repeat with aStage in stagesToDelete
		set stagesText to stagesText & aStage & " "
	end repeat
	display dialog ("No events matching: " & stagesText & return & "Date window: 2026-06-10..2026-07-20") buttons {"OK"} default button "OK"
	return
end if

set calList to ""
repeat with c in hitCalendars
	set calList to calList & "  • " & c & return
end repeat

set sampleText to ""
repeat with s in samples
	set sampleText to sampleText & "  • " & s & return
end repeat

display dialog ("Found " & n & " events in:" & return & calList & return & "Sample:" & return & sampleText & return & "Delete all " & n & "?") buttons {"Cancel", "Delete"} default button "Cancel" with icon caution

if button returned of result is "Delete" then
	set deleted to 0
	tell application "Calendar"
		with timeout of 600 seconds
			repeat with ev in toDelete
				try
					delete ev
					set deleted to deleted + 1
				end try
			end repeat
		end timeout
	end tell
	display dialog ("Deleted " & deleted & " of " & n & " events.") buttons {"OK"} default button "OK"
end if
