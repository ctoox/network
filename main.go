package main

import (
	"flag"
	"log"
	"net/http"
	"time"

	"netsim/internal/net"
	"netsim/internal/server"
)

func main() {
	port := flag.String("port", "8080", "listen port")
	flag.Parse()

	world := net.NewWorld(func(dev *net.Device, iface *net.Iface, pkt *net.Packet, action, detail string) {
		// event log hook (later: push to frontend)
	})
	server.BuildDemoTopology(world)
	world.Start()

	// keep the sim clock ticking so ARP expiry works
	go func() {
		for {
			time.Sleep(100 * time.Millisecond)
			world.Advance(100 * time.Millisecond)
			for _, d := range world.Devices() {
				d.Net.ARP.Prune(world.Now())
			}
		}
	}()

	srv := server.New(world)

	http.HandleFunc("/ws", srv.HandleWebSocket)
	http.HandleFunc("/api/topology", srv.HandleTopology)
	http.Handle("/", http.FileServer(http.Dir("web")))

	log.Printf("netsim listening on http://localhost:%s", *port)
	if err := http.ListenAndServe(":"+*port, nil); err != nil {
		log.Fatal(err)
	}
}