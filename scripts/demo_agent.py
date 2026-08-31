"""Terminal demo with real recommendations and inspectable scoring evidence."""
import argparse

from starter.agent import Agent
from scripts.retrieval_experiment import EXPERIMENTS


class DemoAgent(Agent):
    def _reranked_candidates(self, query, state):
        result = super()._reranked_candidates(query, state)
        self.demo_candidates = {item.parent_asin: item for item in result}
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=EXPERIMENTS, default="optimized")
    parser.add_argument("--scripted", action="store_true")
    args = parser.parse_args()
    agent = DemoAgent(improvements=EXPERIMENTS[args.experiment])
    agent.reset("demo", {})
    examples = iter([
        "I need women's running shoes for travel.",
        "Water-resistant, comfortable, under $80.",
        "Actually white sneakers instead, still for travel.",
    ])
    print(f"Ready ({agent.startup_seconds:.2f}s startup). Type /quit to exit.")
    try:
        for turn in range(1, 11):
            message = next(examples, "/quit") if args.scripted else input("\nYou: ")
            if message.strip() == "/quit":
                break
            if args.scripted:
                print("\nYou:", message)
            response = agent.respond("demo", message, turn, 10)
            print("Agent:", response["message"])
            trace = agent.last_trace()
            print("Active query:", trace["rewritten_query"])
            print("Question decision:", trace["question_reason"])
            print(f"Turn latency: {trace['stage_milliseconds']['total']:.1f} ms")
            for rank, item in enumerate(response["recommendations"], 1):
                identifier = item["parent_asin"]
                product = agent._feature_store.describe(identifier) if agent._feature_store else None
                print(f"{rank}. {product['title'] if product else identifier} [{identifier}]")
                if rank <= 3 and product:
                    print("   Price:", product["price"] if product["price"] is not None else "unknown; budget not verified")
                    candidate = getattr(agent, "demo_candidates", {}).get(identifier)
                    if candidate and candidate.features:
                        weights = agent.phase3_config.feature_weights
                        contributions = [(name, value * getattr(weights, name)) for name, value in candidate.features]
                        significant = sorted(contributions, key=lambda pair: -abs(pair[1]))[:4]
                        print(f"   Retrieval rank {candidate.fresh_rank}; weighted evidence:",
                              ", ".join(f"{name}={value:+.3f}" for name, value in significant))
            print("Note: score contributions explain the algorithm, not guaranteed product suitability.")
    except (EOFError, KeyboardInterrupt):
        print("\nDemo ended.")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
