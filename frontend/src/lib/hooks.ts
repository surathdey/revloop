import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "./api";

export function useFetch<T = any>(url: string | null, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<number>(0);
  const load = useCallback(async () => {
    if (!url) return;
    setLoading(true);
    try {
      setData((await api.get(url)).data);
      setStatus(200);
    } catch (e: any) {
      setStatus(e?.response?.status || 500);
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, ...deps]);
  useEffect(() => {
    load();
  }, [load]);
  return { data, loading, reload: load, setData, status };
}

export async function act(fn: () => Promise<any>, ok?: string) {
  try {
    const r = await fn();
    if (ok) toast.success(ok);
    return r?.data ?? true;
  } catch (e) {
    toast.error(errMsg(e));
    return null;
  }
}
