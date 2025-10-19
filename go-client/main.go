// Minimal Go client for the relay gRPC control plane.
//
// Usage (with temporal dev server, relay worker and grpc_server running):
//
//	go run . -task "compute 6*7 and report"
//
// Starts a pipeline, polls status until it pauses for approval, approves
// it, then polls until completion.
package main

import (
	"context"
	"flag"
	"log"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/adarsh-sgh/relay/go-client/relaypb"
)

func main() {
	addr := flag.String("addr", "localhost:50051", "relay grpc server address")
	task := flag.String("task", "compute 6*7 and report", "task for the agent")
	flag.Parse()

	conn, err := grpc.NewClient(*addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("dial: %v", err)
	}
	defer conn.Close()
	client := pb.NewRelayControlClient(conn)

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()

	run, err := client.StartRun(ctx, &pb.StartRunRequest{Task: *task, RequireApproval: true})
	if err != nil {
		log.Fatalf("start: %v", err)
	}
	log.Printf("started workflow %s", run.WorkflowId)

	waitFor(ctx, client, run.WorkflowId, "awaiting_approval")

	if _, err := client.Approve(ctx, &pb.ApproveRequest{WorkflowId: run.WorkflowId}); err != nil {
		log.Fatalf("approve: %v", err)
	}
	log.Printf("approved")

	waitFor(ctx, client, run.WorkflowId, "completed")
	log.Printf("workflow %s completed", run.WorkflowId)
}

func waitFor(ctx context.Context, client pb.RelayControlClient, id, want string) {
	for {
		st, err := client.GetStatus(ctx, &pb.StatusRequest{WorkflowId: id})
		if err != nil {
			log.Fatalf("status: %v", err)
		}
		log.Printf("status: %s", st.Status)
		if st.Status == want {
			return
		}
		select {
		case <-ctx.Done():
			log.Fatalf("timed out waiting for %s", want)
		case <-time.After(500 * time.Millisecond):
		}
	}
}
