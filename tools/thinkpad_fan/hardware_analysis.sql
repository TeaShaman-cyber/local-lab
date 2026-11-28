-- Only compare fully contained acquisition intervals. Separate capture profiles.
CREATE TEMP VIEW matched AS
SELECT p.*,a.sha,a.offset_ns,
  (ra=1 AND rb=1 AND ta BETWEEN 1 AND 127 AND tb BETWEEN 1 AND 127 AND abs(ta-tb)<=5) tempdual
FROM poll_tach_quality p JOIN poll_wall_times w USING(run_id,seq)
JOIN trace_anchors a ON p.begin_ms>=a.tick_start+32 AND p.end_ms<=a.tick_end-32
  AND abs(w.wall_ns-(a.offset_ns+p.now*1000000))<=2000000000
WHERE p.end_ms>p.begin_ms;
CREATE TEMP VIEW activity AS
SELECT p.run_id,p.seq,p.sha,p.fresh,p.tempdual,p.tach_quality,p.end_ms-p.begin_ms AS duration_ms,
 count_if(e.provider_name='Microsoft-Windows-Kernel-Acpi') acpi,
 count_if(e.provider_name='Microsoft-Windows-WLAN-Driver') wlan
FROM matched p LEFT JOIN events e ON e.sha=p.sha AND e.timestamp_ns BETWEEN
 p.offset_ns+p.begin_ms*1000000 AND p.offset_ns+p.end_ms*1000000
GROUP BY ALL;
SELECT left(sha,12) trace,fresh,tempdual,tach_quality,count(*) n,round(median(duration_ms),1) median_ms,
 round(avg(acpi*1000.0/duration_ms),2) acpi_per_s,round(avg(wlan*1000.0/duration_ms),2) wlan_per_s
FROM activity GROUP BY ALL ORDER BY trace,n DESC;
SELECT count(*) polls,count_if(cpu_valid=1 AND now>=cpu_time AND now-cpu_time<=500) msr_fresh,
 count(tach_rpm) accepted_rpm
FROM poll_tach_quality;
SELECT tach_quality,count(*) n,count(tach_rpm) measured,count_if(tach_rpm=0) observed_zero
FROM poll_tach_quality GROUP BY tach_quality ORDER BY tach_quality;
