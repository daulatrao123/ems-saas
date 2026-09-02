  const [events, setEvents] = useState<any[]>([]);
  const [lastEventId, setLastEventId] = useState<number>(0);

  const fetchEvents = useCallback(async () => {
    if (!societyId) return;
    try {
      const res = await api.get(`/api/admin/pi-events?society_id=${societyId}&last_id=${lastEventId}`);
      if (res.data.events.length > 0) {
        setEvents((prev) => [...prev, ...res.data.events].slice(-100)); // Keep last 100
        setLastEventId(res.data.last_id);
      }
    } catch (error) {
      console.error("Failed to fetch events", error);
    }
  }, [societyId, lastEventId]);

  useEffect(() => {
    fetchEvents();
    const interval = setInterval(fetchEvents, 5000);
    return () => clearInterval(interval);
  }, [fetchEvents]);