  const [events, setEvents] = useState<any[]>([]);
  const [lastEventId, setLastEventId] = useState<number>(0);

  const fetchEvents = useCallback(async () => {
    try {
      const res = await api.get(`/api/member/events?last_id=${lastEventId}`);
      if (res.data.events.length > 0) {
        setEvents((prev) => [...prev, ...res.data.events].slice(-50));
        setLastEventId(res.data.last_id);
      }
    } catch (error) {
      console.error("Failed to fetch events", error);
    }
  }, [lastEventId]);

  useEffect(() => {
    fetchEvents();
    const interval = setInterval(fetchEvents, 10000);
    return () => clearInterval(interval);
  }, [fetchEvents]);