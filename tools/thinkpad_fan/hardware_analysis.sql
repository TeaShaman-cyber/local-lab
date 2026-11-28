-- Only compare fully contained acquisition intervals. Separate capture profiles.
CREATE TEMP VIEW matched AS
SELECT p.*,a.sha,a.offset_ns,
  (ok=1 AND ordered=1 AND epoch=poll_epoch AND begin_ms>0 AND now>=end_ms AND now-begin_ms<=2000) fresh,
  (ra=1 AND rb=1 AND ta BETWEEN 1 AND 127 AND tb BETWEEN 1 AND 127 AND abs(ta-tb)<=5) tempdual,
  (lo_a=1 AND hi_a=1 AND lo_b=1 AND hi_b=1 AND rpm_a BETWEEN 0 AND 8191 AND rpm_b BETWEEN 0 AND 8191 AND abs(rpm_a-rpm_b)<=200 AND (rpm_a=0)=(rpm_b=0)) tachdual
FROM polls p JOIN trace_anchors a ON p.begin_ms>=a.tick_start+32 AND p.end_ms<=a.tick_end-32
WHERE p.end_ms>p.begin_ms;
CREATE TEMP VIEW activity AS
SELECT p.run_id,p.seq,p.sha,p.fresh,p.tempdual,p.tachdual,p.end_ms-p.begin_ms AS duration_ms,
 count_if(e.provider_name='Microsoft-Windows-Kernel-Acpi') acpi,
 count_if(e.provider_name='Microsoft-Windows-WLAN-Driver') wlan
FROM matched p LEFT JOIN events e ON e.sha=p.sha AND e.timestamp_ns BETWEEN
 p.offset_ns+p.begin_ms*1000000 AND p.offset_ns+p.end_ms*1000000
GROUP BY ALL;
SELECT left(sha,12) trace,fresh,tempdual,tachdual,count(*) n,round(median(duration_ms),1) median_ms,
 round(avg(acpi*1000.0/duration_ms),2) acpi_per_s,round(avg(wlan*1000.0/duration_ms),2) wlan_per_s
FROM activity GROUP BY ALL ORDER BY trace,n DESC;
SELECT count(*) polls,count_if(cpu_valid=1 AND now>=cpu_time AND now-cpu_time<=500) msr_fresh,
 count_if((lo_a=1 AND hi_a=1 AND rpm_a BETWEEN 0 AND 8191) OR (lo_b=1 AND hi_b=1 AND rpm_b BETWEEN 0 AND 8191)) at_least_one_rpm
FROM polls;
