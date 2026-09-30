"use client";
import TasksContainer from "@/components/tasks/TasksContainer";
import TasksSidebar from "@/components/tasks/TasksSidebar";
import { ActivityType } from "@/models/activity.model";
import { useState, useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { getActivityTypesWithTaskCounts } from "@/actions/task.actions";

type ActivityTypeWithCount = {
  id: string;
  label: string;
  value: string;
  taskCount: number;
};

type TasksPageClientProps = {
  activityTypes: ActivityType[];
  activityTypesWithCounts: ActivityTypeWithCount[];
  totalTasks: number;
};

function TasksPageClient({
  activityTypes,
  activityTypesWithCounts,
  totalTasks,
}: TasksPageClientProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [filterKey, setFilterKey] = useState<string | undefined>(
    searchParams.get("activityType") ?? undefined,
  );
  const [sidebarCounts, setSidebarCounts] =
    useState<ActivityTypeWithCount[]>(activityTypesWithCounts);
  const [sidebarTotal, setSidebarTotal] = useState<number>(totalTasks);

  const onFilterChange = (filter: string | undefined) => {
    setFilterKey(filter);
    const params = new URLSearchParams(searchParams.toString());
    if (filter) {
      params.set("activityType", filter);
    } else {
      params.delete("activityType");
    }
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, {
      scroll: false,
    });
  };

  const refreshSidebarCounts = useCallback(async () => {
    const result = await getActivityTypesWithTaskCounts();
    if (result?.success) {
      setSidebarCounts(result.data);
      setSidebarTotal(result.totalTasks);
    }
  }, []);

  return (
    <div className="col-span-3 flex h-full">
      <TasksSidebar
        activityTypes={sidebarCounts}
        totalTasks={sidebarTotal}
        selectedFilter={filterKey}
        onFilterChange={onFilterChange}
      />
      <div className="flex-1">
        <TasksContainer
          activityTypes={activityTypes}
          filterKey={filterKey}
          onTasksChanged={refreshSidebarCounts}
        />
      </div>
    </div>
  );
}

export default TasksPageClient;
